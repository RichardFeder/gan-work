from __future__ import print_function
import numpy as np
import h5py
import argparse
import os
import random
import torch
import time
import torch.nn as nn
import torch.nn.parallel
import torch.backends.cudnn as cudnn
import torch.optim as optim
import torchvision.utils as vutils
from torchvision import transforms
from torch.utils import data
from power_spec import *
from models import *
from helpers import *


opt = get_parsed_arguments('nbody')

richard_workdir = '/work/06224/rfederst/maverick2/results/'

#opt = parser.parse_known_args()[0]
if not torch.cuda.is_available():
    opt.cuda=False
    
device = torch.device("cuda:0" if opt.cuda else "cpu")
nc = 1 # just one channel for images                                                                                                                
real_label = 1
fake_label = 0
n_cond_params = 0

#sizes = make_size_array(opt.cubedim, 4)
sizes = np.array([8,4,2,1])

redshift_bins = np.array([10., 7.5, 5., 3., 2., 1.5, 1., 0.5, 0.25, 0.]) 
redshift_strings = np.array(["%3.3d"%(i) for i in xrange(len(redshift_bins))])
if opt.redshift_idxs is None:
    opt.redshift_idxs = np.array([i for i in xrange(len(redshift_bins))])
    opt.redshift_bins = redshift_bins[opt.redshift_idxs]
    output1shape = (opt.cubedim/2, opt.cubedim/2, opt.cubedim/2) # for z feature maps in discriminator
if opt.redshift_code:
    n_cond_params = 1

print('Redshifts:', redshift_bins)
print('Redshift strings:', redshift_strings)


timestr, new_dir, frame_dir, fake_dir = setup_result_directories()
save_params(new_dir, opt)

#Initialize Generator and Discriminator                                                                                                              
netG = DC_Generator3D(opt.ngpu, 1, opt.latent_dim+n_cond_params, opt.ngf, sizes).to(device)
netD = DC_Discriminator3D(opt.ngpu, 1, opt.ndf, sizes, n_cond_features = n_cond_params).to(device)
if opt.netG=='':
    netG.apply(weights_init)
    netD.apply(weights_init)
else:
    netG.load_state_dict(torch.load(richard_workdir+opt.netG+'/netG', map_location=device))
    netD.load_state_dict(torch.load(richard_workdir+opt.netG+'/netD', map_location=device))
    netG.train()
    netD.train()

print(netG)
print(netD)

# Set loss                                                                                                                                                                             
criterion = nn.BCELoss()

# fixed noise used for sample generation comparisons at different points in training                                                                                                   
fixed_noise = torch.randn(4, opt.latent_dim, 1, 1, 1, device=device)

# set up optimizers                                                                                                                                                                    
optimizerD = optim.Adam(netD.parameters(), lr=opt.lr_d, betas=(opt.b1, opt.b2))
optimizerG = optim.Adam(netG.parameters(), lr=opt.lr_g, betas=(opt.b1, opt.b2))

lossD_vals, lossG_vals = [[], []]

def make_feature_maps(array_vals, featureshape, device):
    feature_maps = torch.from_numpy(np.array([np.full(featureshape, val) for val in array_vals])).to(device)
    return torch.unsqueeze(feature_maps, 1).float()


class GAN_optimization():
    def __init__(self, optimizerD, optimizerG, netD, netG):
        self.optimizerD = optimizerD
        self.optimizerG = optimizerG
        self.netD = netD
        self.netG = netG
        self.criterion = criterion
        if opt.trainSize > 0:
                self.batchSize = np.minimum(opt.trainSize, opt.batchSize)
        else:
                self.batchSize = opt.batchSize


    def discriminator_step(self, real_cpu, zs=None):
        self.netD.zero_grad()

        label = torch.full((self.batchSize,), real_label, device=device)
        # reshape needed for images with one channel                                                                                                                          
        real_cpu = torch.unsqueeze(real_cpu, 1).float()
        if zs is not None:
            output = self.netD(real_cpu, zfeatures=make_feature_maps(zs, output1shape, device))
        else:
            output = self.netD(real_cpu)
        errD_real = self.criterion(output, label)
        errD_real.backward()
        D_x = output.mean().item()

        # train with fake                                                                                                                                               

        noise = torch.randn(self.batchSize, opt.latent_dim, 1, 1, 1, device=device)
        if zs is not None:
            fake_zs = np.random.choice(redshift_bins, opt.batchSize)
            noise = torch.cat((noise, torch.from_numpy(fake_zs).float().view(-1,1,1,1,1).to(device)), 1)
        fake = self.netG(noise)
        label.fill_(fake_label)

        if zs is not None:
            output = self.netD(fake.detach(), zfeatures=make_feature_maps(fake_zs, output1shape, device))
        else:
            output = self.netD(fake.detach())
        
        errD_fake = self.criterion(output, label)
        errD_fake.backward()
        D_G_z1 = output.mean().item()

        errD = errD_real + errD_fake
        #print('D_G_z1:', D_G_z1)
        if D_G_z1 > 0.2:
            self.optimizerD.step()

        return errD, D_x, D_G_z1

    def generator_step(self, zs=None):
        noise = torch.randn(self.batchSize, opt.latent_dim, 1, 1, 1, device=device)
        if zs is not None:
            fake_zs = np.random.choice(redshift_bins, opt.batchSize)
            noise = torch.cat((noise, torch.from_numpy(fake_zs).float().view(-1,1,1,1,1).to(device)), 1)
            #noise = torch.cat((noise, torch.from_numpy(fake_zs).float()), 1)
        fake = self.netG(noise)
        label = torch.full((self.batchSize,), real_label, device=device)
        self.netG.zero_grad()
        label.fill_(real_label)  # fake labels are real for generator cost  

        if zs is not None:
            output = self.netD(fake, zfeatures=make_feature_maps(fake_zs, output1shape, device))
        else:
            output = self.netD(fake)

        errG = self.criterion(output, label)
        errG.backward()
        D_G_z2 = output.mean().item()
        self.optimizerG.step()
        return errG, D_G_z2

    def single_train_step(self, real_cpu, zs=None):
        errD, D_x, D_G_z1 = self.discriminator_step(real_cpu, zs=zs)
        for i in xrange(opt.n_genstep):
                errG, D_G_z2 = self.generator_step(zs=zs)
        return errD, errG, D_x, D_G_z1, D_G_z2

ganopt = GAN_optimization(optimizerD, optimizerG, netD, netG)


if opt.redshift_code:
    sim_boxes, zlist = load_in_simulations(opt)
else:
    sim_boxes = load_in_simulations(opt)
        
print(len(sim_boxes), 'loaded into memory..')


for i in xrange(opt.n_epochs):
    lossGs, lossDs = [], []
    sim_idxs = np.arange(len(sim_boxes))
    while len(sim_idxs)>0:
        choice = np.random.choice(sim_idxs, opt.batchSize, replace=False)
        if opt.redshift_code:
            zs = zlist[choice]
        dat = []
        for c in choice:
            nrots = np.random.choice([0, 1, 2, 3], 3)
            # random rotation wrt each plane
            dat.append(np.rot90(np.rot90(np.rot90(sim_boxes[c], nrots[0], axes=(0,1)), nrots[1], axes=(1,2)), nrots[2], axes=(0,2)))

        sim_idxs = np.array([x for x in sim_idxs if x not in choice])
        real_cpu = torch.from_numpy(np.array(dat)).to(device)
        if opt.redshift_code:
            zs = zlist[choice]
            output1shape = (opt.cubedim/2, opt.cubedim/2, opt.cubedim/2)

            errD, errG, D_x, D_G_z1, D_G_z2 = ganopt.single_train_step(real_cpu, zs=zs)
        else:
            errD, errG, D_x, D_G_z1, D_G_z2 = ganopt.single_train_step(real_cpu)
        
        print('errG:', errG.item(),'errD:', errD.item(), 'D_G_z1:', D_G_z1)
        
        assert errG.item() != 0.0 # these make sure we're not falling into mode collapse or an outperforming discriminator
        assert errD.item() != 0.0
        
        lossGs.append(errG.item())
        lossDs.append(errD.item())

    lossG_vals.append(np.mean(lossGs))
    lossD_vals.append(np.mean(lossDs))

    print('[%d/%d] Loss_D: %.4f Loss_G: %.4f D(x): %.4f D(G(z)): %.4f / %.4f' % (i, opt.n_epochs, lossD_vals[i], lossG_vals[i], D_x, D_G_z1, D_G_z2))

    save_nn(ganopt.netG, new_dir+'/netG_epoch_'+str(i))
    save_nn(ganopt.netD, new_dir+'/netD_epoch_'+str(i))
    #fake = ganopt.netG(fixed_noise)

    #arr = torch.squeeze(fake).detach().cpu().numpy()[0,:,:,0]
    try:
        arr = torch.squeeze(fake).detach().cpu().numpy()[:,:,:,10]
        for j in xrange(4):
            plt.subplot(2,2,j+1)
            plt.imshow(arr[j])
            plt.colorbar()
        print('saving to', fake_dir+'/fake_sampels_panel_'+str(i)+'.png')
        plt.savefig(fake_dir+'/fake_samples_panel_i_'+str(i)+'.png')
        plt.close()
        #save_nn(ganopt.netG, new_dir+'/netG_epoch_'+str(i))
        #save_nn(ganopt.netD, new_dir+'/netD_epoch_'+str(i))
    except:
        print('error in saving images for some reason')

save_nn(ganopt.netG, new_dir+'/netG')
save_nn(ganopt.netD, new_dir+'/netD')

plot_loss_iterations(np.array(lossD_vals), np.array(lossG_vals), new_dir)
