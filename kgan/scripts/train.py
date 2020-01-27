"""
This is a script trains a model or
set of models based on a config yaml
file.


"""

import numpy as np

import os
import sys
import yaml
import ast
import h5py
import math

from importlib import import_module
from functools import partial

from mpi4py import MPI
comm = MPI.COMM_WORLD
rank = comm.rank
size = comm.size

import os
os.environ['CUDA_VISIBLE_DEVICES'] = str(rank)

from kgan.tools.delta import *
from kgan.net.gp_loss import *

from keras import optimizers
import keras.layers as kl
import keras.models as km

import tensorflow as tf
import keras.backend as K

def load_config(yaml_file):

    with open(yaml_file, 'r') as tfile:
        file_str = tfile.read()
        
    return yaml.load(file_str)


if __name__ == '__main__':

    # Get info from config
    conf_files = sys.argv[1:]
    conf_file = conf_files[rank]
    print(rank, conf_file)
    config = load_config(conf_file)
    
    # Load the data
    data_dict = config['paths_to_data']
    delta = []
    for ri in range(size):
        if ri == rank:
            for dkey in data_dict.keys():
                with h5py.File(data_dict[dkey], 'r') as dfile:
                    for dk in list(dfile.keys()):
                        delta_i = squash(dfile[dk][:])
                        delta_i = np.array(split3d(delta_i, 8))
                        delta.extend(delta_i)
            delta = np.array(delta)
        comm.Barrier()

    print("Data has shape: %s" % str(delta.shape))

    # Get the training params
    train_batch_size = int(config['train_config']['train_batch_size'])
    image_shape = ast.literal_eval(config['train_config']['image_shape'])
    latent_dim = int(config['train_config']['latent_dim'])
    nepochs = int(config['train_config']['nepochs'])
    ############
    # HARDCODE #
    ############
    try:
        n_critic = int(config['train_config']['n_critic'])
    except KeyError:
        n_critic = 5
    try:
        gp_weight = int(config['train_config']['gp_weight'])
    except KeyError:
        gp_weight = 10
    n_samps = delta.shape[0]

    # One GPU for each rank
    #with tf.device('/gpu:%i' % rank):
    if True:
        # Build the models
        generator_module = import_module(config['net_config']['generator']['module'])
        critic_module = import_module(config['net_config']['critic']['module'])

        generator_args = {}
        for key, val in config['net_config']['generator']['args'].iteritems():
            try:
                generator_args[key] = ast.literal_eval(val)
            except ValueError:
                generator_args[key] = val
        critic_args = {}
        for key, val in config['net_config']['critic']['args'].iteritems():
            try:
                critic_args[key] = ast.literal_eval(val)
            except ValueError:
                critic_args[key] = val

        generator_args['base_features'] = generator_args['base_features'] * 2**generator_args['nlevels']
        generator = generator_module.get_generator(input_shape=(latent_dim,), image_shape=image_shape,
                                                   **generator_args)
        critic = critic_module.get_critic(input_shape=image_shape+(1,), **critic_args)

        # Build the optimizer
        disc_op_args = {}
        gen_op_args = {}
        for key, val in config['optimizer_config']['args'].iteritems():
            try:
                gen_op_args[key] = ast.literal_eval(val)
                disc_op_args[key] = ast.literal_eval(val)
                if key == 'decay': # Needs to be adjusted to n_critic
                    gen_op_args[key] = gen_op_args[key] * n_critic
            except ValueError:
                gen_op_args[key] = val
                disc_op_args[key] = val

        gen_optimizer = getattr(optimizers, config['optimizer_config']['name'])(**gen_op_args)
        disc_optimizer = getattr(optimizers, config['optimizer_config']['name'])(**disc_op_args)

        #-------------------------------
        # Construct Computational Graph
        #       for the Critic
        #-------------------------------

        # Freeze generator's layers while training critic
        generator.trainable = False

        # Image input (real sample)
        real_img = kl.Input(shape=image_shape+(1,))

        # Noise input
        z_disc = kl.Input(shape=(latent_dim,))
        # Generate image based of noise (fake sample)
        fake_img = generator(z_disc)

        # Discriminator determines validity of the real and fake images
        fake = critic(fake_img)
        valid = critic(real_img)

        # Construct weighted average between real and fake images
        interpolated_img = RandomWeightedAverage()([real_img, fake_img])
        # Determine validity of weighted sample
        validity_interpolated = critic(interpolated_img)

        # Use Python partial to provide loss function with additional
        # 'averaged_samples' argument
        partial_gp_loss = partial(gradient_penalty_loss,
                                  averaged_samples=interpolated_img)
        partial_gp_loss.__name__ = 'gradient_penalty' # Keras requires function names

        critic_model = km.Model(inputs=[real_img, z_disc],
                                outputs=[valid, fake, validity_interpolated])
        critic_model.compile(loss=[wasserstein_loss,
                                   wasserstein_loss,
                                   partial_gp_loss],
                             optimizer=disc_optimizer,
                             loss_weights=[1, 1, gp_weight])
        #-------------------------------
        # Construct Computational Graph
        #         for Generator
        #-------------------------------

        # For the generator we freeze the critic's layers
        critic.trainable = False
        generator.trainable = True

        # Sampled noise for input to generator
        z_gen = kl.Input(shape=(latent_dim,))
        # Generate images based of noise
        img = generator(z_gen)
        # Discriminator determines validity
        valid = critic(img)
        # Defines generator model
        generator_model = km.Model(z_gen, valid)
        generator_model.compile(loss=wasserstein_loss, optimizer=gen_optimizer)
    
        #-------------------------------
        #        Ok now train
        #-------------------------------

        # Adversarial ground truths
        batch_size = train_batch_size
        valid = -np.ones((batch_size, 1))
        fake =  np.ones((batch_size, 1))
        dummy = np.zeros((batch_size, 1)) # Dummy gt for gradient penalty
    
        d_hist = []
        g_hist = []

        # Set learning rate schedule
        lr_gamma       = float(config['optimizer_config']['lr_gamma'])
        lr_sched_epoch = np.arange(0, nepochs, 5)
        #lr_sched       = gen_op_args['lr']*lr_gamma**lr_sched_epoch 
        

        for epoch in range(nepochs):

            if lr_gamma < 1.0:
                print("Applying lr schedule: ",)
                # Apply the learning rate schedule
                if epoch in lr_sched_epoch:
                    #lsei = list(lr_sched_epoch).index(epoch)
                    lr = K.get_value(critic_model.optimizer.lr)
                    K.set_value(critic_model.optimizer.lr, lr*lr_gamma)
                    K.set_value(generator_model.optimizer.lr, lr*lr_gamma)

    
            choice = np.arange(n_samps)
            np.random.shuffle(choice)
    
            niter = len(choice)//batch_size//n_critic
    
            for it in range(niter):
                for ic in range(n_critic):

                    # ---------------------
                    #  Train Discriminator
                    # ---------------------

                    # Get some true images
                    s0 = it*batch_size*n_critic + ic*batch_size
                    s1 = it*batch_size*n_critic + (ic+1)*batch_size
                    imgs = delta[choice[s0:s1]]
                    imgs = imgs.reshape((batch_size,) + image_shape + (1,))
            
                    #Random reflections
                    if np.random.uniform() >= 0.5:
                        imgs = imgs[:, ::-1]
                    if np.random.uniform() >= 0.5:
                        imgs = imgs[:, :, ::-1]
                    if np.random.uniform() >= 0.5:
                        imgs = imgs[:, :, :, ::-1]

                    # Sample generator input
                    noise = np.random.normal(0, 1, (batch_size, latent_dim))
                    # Train the critic
                    d_loss = critic_model.train_on_batch([imgs, noise],
                                                         [valid, fake, dummy])

                # ---------------------
                #  Train Generator
                # ---------------------

                g_loss = generator_model.train_on_batch(noise, valid)

                d_hist.append(d_loss[0])
                g_hist.append(g_loss)

                print ("%i - %d: %i/%i: [D loss: %f] [G loss: %f]" % 
                       (rank, epoch, it, niter, d_loss[0], g_loss))

                #test_img = generator.predict(np.random.normal(0, 1, (1, latent_dim))).reshape(img_shape)
                #plt.imshow(test_img[32, :, :, 0])
                #colorbar()
                #display.display(gcf())
        
            if (epoch in [5, 9, 11, 15, 17, 19, 21, 25, 30, 33]) or (epoch >= 35):
                # Checkpoint the generator
                generator.save(config['save_model'] + '-%03d' % epoch)

        # Save the history
        with h5py.File(config['save_history']) as hfile:
            hfile.create_dataset('g_loss', data=np.array(d_hist))
            hfile.create_dataset('d_loss', data=np.array(g_hist))
