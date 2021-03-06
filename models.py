from torch.autograd import Variable, grad
import torch.nn as nn
import torch
import torch.nn.functional as F
import numpy as np
import cPickle as pickle
from helpers import *

# custom weights initialization called on netG and netD
def weights_init(m):
    classname = m.__class__.__name__
    if classname.find('Conv') != -1:
        m.weight.data.normal_(0.0, 0.02)
    elif classname.find('BatchNorm') != -1:
        m.weight.data.normal_(1.0, 0.02)
        m.bias.data.fill_(0)

def restore_conv_nn_new(filepath, sizes):
    # get config parameters from run and use to construct Perceptron object
    filen = open(filepath+'/params.txt','r')
    pdict = pickle.load(filen)
    model = DC_Generator(pdict['ngpu'], 1, pdict['latent_dim'], pdict['ngf'], sizes)
    
    model.load_state_dict(torch.load(filepath+'/netG', map_location='cpu'))
    model.eval()
    
    return model, pdict

class Perceptron(torch.nn.Module):
    def __init__(self, sizes, activation, final=None, sigmoid=False):
        super(Perceptron, self).__init__() # what does this line do?
        layers = []
        self.sigmoid = sigmoid
        print 
        print 'Initializing Neural Net'
        print 'Activation: '+activation
        for i in xrange(len(sizes) - 1):
            print 'Layer', i, ':', sizes[i], sizes[i+1]
            layers.append(nn.Linear(sizes[i], sizes[i + 1]))
            if i != (len(sizes) - 2):
                if activation=='ReLU':
                    layers.append(nn.ReLU())
                elif activation=='LeakyReLU':
                    layers.append(nn.LeakyReLU()) # leaky relu, alpha=0.01
                elif activation=='ELU':
                    layers.append(nn.ELU())
                elif activation=='PReLU':
                    layers.append(nn.PReLU())

        if final is not None:
            layers.append(final())
        self.net = nn.Sequential(*layers)

    def forward(self, x):
        output = self.net(x)
        if self.sigmoid:
            output = F.sigmoid(output)
        return output



class DC_Generator(nn.Module):
    def __init__(self, ngpu, nc, nz, ngf, sizes):
        super(DC_Generator, self).__init__()
        self.ngpu = ngpu
        print('ngpu:', ngpu)
        layers = []

        for i, size in enumerate(sizes):
            print(i, size)
            if i==0:
                layers.append(nn.ConvTranspose2d(nz, ngf*int(size), 4, stride=1, padding=0, bias=False))
            else:
                layers.append(nn.ConvTranspose2d(outc, ngf*int(size), 2, stride=2, padding=0, bias=False))
          #      layers.append(nn.ConvTranspose2d(outc, ngf*int(size), 4, stride=2, padding=1, bias=False))
            outc = ngf*int(size) 
            layers.append(nn.BatchNorm2d(outc))
            layers.append(nn.ReLU(True))
        layers.append(nn.ConvTranspose2d(outc, nc, 4, 2, 1, bias=False))
        layers.append(nn.Tanh())
        

        self.main = nn.Sequential(*layers)

    def forward(self, input):
        if input.is_cuda and self.ngpu > 1:
            #print('heeeeere')
            output = nn.parallel.data_parallel(self.main, input, range(self.ngpu))
        else:
            output = self.main(input)

        return output

class DC_Discriminator(nn.Module):
    def __init__(self, ngpu, nc, ndf, sizes, code_dim=0, cond_dim=0):
        super(DC_Discriminator, self).__init__()
        self.ngpu = ngpu
        self.code_dim = code_dim
        self.cond_dim = cond_dim
        layers = []
        for i, size in enumerate(np.flip(sizes, 0)):
            if i==0:
                layers.append(nn.Conv2d(nc, ndf*int(size), 4, 2, 1, bias=False))
            else:
                layers.append(nn.Conv2d(outc, ndf*int(size), 4, 2, 1, bias=False))
                layers.append(nn.BatchNorm2d(ndf*int(size)))
            outc = ndf*int(size)
            layers.append(nn.LeakyReLU(0.2, inplace=True))

        # separating last layer+activation so infogan can take feature map before sigmoid activation

        final_layers = []
        final_layers.append(nn.Conv2d(outc, 1, 4, 1, 0, bias=False))
        final_layers.append(nn.Sigmoid())

        #layers.append(nn.Conv2d(outc, 1, 4, 1, 0, bias=False))
        #layers.append(nn.Sigmoid())

        self.main = nn.Sequential(*layers)
        self.final = nn.Sequential(*final_layers)


        if self.code_dim > 0:
            self.latent_layer = nn.Sequential(nn.Conv2d(outc, self.code_dim, 4, 1, 0, bias=False))

        if self.cond_dim > 0:
            pass
    def forward(self, input):
        if input.is_cuda and self.ngpu > 1:
            #print('heere')
            out = nn.parallel.data_parallel(self.main, input, range(self.ngpu))
            #output = nn.parallel.data_parallel(self.final, out, range(self.ngpu))
            #output = nn.parallel.data_parallel(self.main, input, range(self.ngpu))
        else:
            out = self.main(input)
            #output = self.final(out)
            #output = self.main(input)
        output = self.final(out)
        if self.code_dim > 0:
            #return output.view(-1, 1).squeeze(1), output.view(-1, 1).squeeze(1)
            latent_code = self.latent_layer(out)
            return output.view(-1, 1).squeeze(1), latent_code.view(-1, 1).squeeze(1)

        else:
            return output.view(-1, 1).squeeze(1)

class DC_Generator3D(nn.Module):
    def __init__(self, ngpu, nc, nz, ngf, sizes, extra_conv_layers=0, endact='tanh'):
        super(DC_Generator3D, self).__init__()
        self.ngpu = ngpu
        layers = []
        kernel_sizes = [4, 4, 4, 4]
        strides = [1, 2, 2, 2]
        paddings = [0, 1, 1, 1]
        
        for i, size in enumerate(sizes):
            if i==0:
                layers.append(nn.ConvTranspose3d(nz, ngf*int(size), kernel_sizes[i], strides[i], paddings[i], bias=False) )
            else:
                layers.append(nn.ConvTranspose3d(outc, ngf*int(size), kernel_sizes[i], strides[i], paddings[i], bias=False) )
         
            outc = ngf*int(size)
            layers.append(nn.BatchNorm3d(outc))
            layers.append(nn.ReLU(True))
            


        #layers.append(nn.Conv3d(outc, outc, 3, stride=1, padding=1, bias=False))
        #layers.append(nn.BatchNorm3d(outc))
        #layers.append(nn.ReLU(True))
        #layers.append(nn.ConvTranspose3d(outc, nc, 2, stride=2, padding=1, bias=False))

        # 4x4x4 filter to get to final size
        layers.append(nn.ConvTranspose3d(outc, outc, 4, stride=2, padding=1, bias=False))
        layers.append(nn.BatchNorm3d(outc))
        layers.append(nn.ReLU(True))
        # 3x3x3 filters at final size, multiple channels 
        layers.append(nn.Conv3d(outc, outc, 3, stride=1, padding=1, bias=False))
        layers.append(nn.BatchNorm3d(outc))
        layers.append(nn.ReLU(True))
        # gets us to one channel
        layers.append(nn.Conv3d(outc, nc, 3, stride=1, padding=1, bias=False))
        #layers.append(nn.BatchNorm3d(nc))
        #layers.append(nn.ReLU(True))
        #layers.append(nn.Conv3d(nc, nc, 3, stride=1, padding=1, bias=False))
        

        # 4x4x4 filter to get to final size and from 32 to 1 channel 
        #layers.append(nn.ConvTranspose3d(outc, nc, 4, stride=2, padding=1, bias=False))
        #layers.append(nn.BatchNorm3d(nc))
        # single 3x3x3 convolutional filter and non-linearity
        #layers.append(nn.ReLU(True))
        #layers.append(nn.Conv3d(nc, nc, 3, stride=1, padding=1, bias=False))
        
#        layers.append(nn.ReLU(True))
#        layers.append(nn.Conv3d(nc, nc, 2, stride=1, padding=1, bias=False))
        
        for i in xrange(extra_conv_layers):
            layers.append(nn.ConvTranspose3d(outc, outc, 3, stride=1, padding=1, bias=False)) # extra layer
            layers.append(nn.BatchNorm3d(outc))
            layers.append(nn.ReLU(True))

        #layers.append(nn.ConvTranspose3d(outc, nc, 4, stride=2, padding=1, bias=False))
        #layers.append(nn.ConvTranspose3d(outc, nc, 2, stride=2, padding=0, bias=False))
        
        if endact=='tanh': 
            layers.append(nn.Tanh())
        elif endact=='softplus':
            layers.append(nn.Softplus())
        self.main = nn.Sequential(*layers)

    def forward(self, input):
        if input.is_cuda and self.ngpu > 1:
            output = nn.parallel.data_parallel(self.main, input, range(self.ngpu))
        else:
            output = self.main(input)
        return output

class DC_Discriminator3D(nn.Module):

    def __init__(self, ngpu, nc, ndf, sizes, device, endact_disc='sigmoid', n_cond_features=0):
        super(DC_Discriminator3D, self).__init__()
        self.ngpu = ngpu
        self.device = device
        layers = []
        first_layer = []

        first_layer.append(nn.Conv3d(nc, ndf*int(sizes[-1]), 3, stride=1, padding=1, bias=False))
        first_layer.append(nn.LeakyReLU(0.2, inplace=True))
        for i, size in enumerate(np.flip(sizes, 0)):

            if i==0:
                
                # remove these three lines to make discriminator slightly less powerful
                #first_layer.append(nn.Conv3d(ndf*int(size), ndf*int(size), 3, stride=1, padding=1, bias=False))
                #first_layer.append(nn.BatchNorm3d(ndf*int(size)))
                #first_layer.append(nn.LeakyReLU(0.2, inplace=True))

                first_layer.append(nn.Conv3d(ndf*int(size), ndf*int(size), 4, stride=2, padding=1, bias=False))
                #first_layer.append(nn.Conv3d(nc, ndf*int(size), 4, stride=2, padding=1, bias=False))
                first_layer.append(nn.BatchNorm3d(ndf*int(size)))
                first_layer.append(nn.LeakyReLU(0.2, inplace=True))
            # if there are conditional parameters this will accommoate an extra feature map 
            elif i==1:
                layers.append(nn.Conv3d(outc+n_cond_features, ndf*int(size), 4, stride=2, padding=1, bias=False))
                layers.append(nn.BatchNorm3d(ndf*int(size)))
                layers.append(nn.LeakyReLU(0.2, inplace=True))
            else:
                layers.append(nn.Conv3d(outc, ndf*int(size), 4, stride=2, padding=1, bias=False))
                layers.append(nn.BatchNorm3d(ndf*int(size)))
                layers.append(nn.LeakyReLU(0.2, inplace=True))
            
            outc = ndf*int(size)

        if endact_disc == 'sigmoid':
            layers.append(nn.Conv3d(outc, 1, 4, stride=1, padding=0, bias=False))
            layers.append(nn.Sigmoid())

        elif endact_disc == 'linear':
            layers.append(nn.Linear(outc, 1))

        self.main = nn.Sequential(*layers)
        self.first = nn.Sequential(*first_layer)

    def forward(self, input, cond=None):
        if input.is_cuda and self.ngpu > 1:
            output1 = nn.parallel.data_parallel(self.first, input, range(self.ngpu))

            if cond is not None:
                cond_features = make_feature_maps(cond, output1[0,0,:,:,:].shape, self.device)
                output1 = torch.cat((output1, cond_features), 1)
            output = nn.parallel.data_parallel(self.main, output1, range(self.ngpu))
        else:  
            output1 = self.first(input)
            output = self.main(output1)
        return output.view(-1, 1).squeeze(1)



class DC_Generator3D_simpler(nn.Module):
    def __init__(self, ngpu, nc, nz, ngf, sizes, extra_conv_layers=0, endact='tanh'):
        super(DC_Generator3D_simpler, self).__init__()
        self.ngpu = ngpu
        self.layers = []

        kernel_sizes = [4, 4, 4, 4]
        strides = [1, 2, 2, 2]
        paddings = [0, 1, 1, 1]
        
        for i, size in enumerate(sizes):
            if i==0:
                self.layers.append(nn.ConvTranspose3d(nz, ngf*int(size), kernel_sizes[i], strides[i], paddings[i], bias=False) )
            else:
                self.layers.append(nn.ConvTranspose3d(outc, ngf*int(size), kernel_sizes[i], strides[i], paddings[i], bias=False) )
            outc = ngf*int(size)
            self.layers.append(nn.BatchNorm3d(outc))
            self.layers.append(nn.ReLU(True))

        for i in xrange(extra_conv_layers):
            self.layers.append(nn.ConvTranspose3d(outc, outc, 3, stride=1, padding=1, bias=False)) # extra layer
            self.layers.append(nn.BatchNorm3d(outc))
            self.layers.append(nn.ReLU(True))

        self.layers.append(nn.ConvTranspose3d(outc, nc, 4, stride=2, padding=1, bias=False))
        
        if endact=='tanh': 
            self.layers.append(nn.Tanh())
        elif endact=='softplus':
            self.layers.append(nn.Softplus())
        self.main = nn.Sequential(*self.layers)

    def forward(self, input, feat_from_last=0):
        if input.is_cuda and self.ngpu > 1:
            if feat_from_last > 0:
                out1 = nn.parallel.data_parallel(nn.Sequential(*self.layers[:-2-3*(feat_from_last-1)]), input, range(self.ngpu))
                output = nn.parallel.data_parallel(nn.Sequential(*self.layers[-2-3*(feat_from_last-1):]), out1, range(self.ngpu))
            else:
                output = nn.parallel.data_parallel(self.main, input, range(self.ngpu))
        else:
            output = self.main(input)
        if feat_from_last > 0:
            return out1, output
        return output

class DC_Discriminator3D_simpler(nn.Module):
    def __init__(self, ngpu, nc, ndf, sizes, device, endact_disc='sigmoid', n_cond_features=0):
        super(DC_Discriminator3D_simpler, self).__init__()
        self.ngpu = ngpu
        self.device = device
        layers = []
        first_layer = []
        for i, size in enumerate(np.flip(sizes, 0)):

            if i==0:
                first_layer.append(nn.Conv3d(nc, ndf*int(size), 4, stride=2, padding=1, bias=False))
                first_layer.append(nn.LeakyReLU(0.2, inplace=True))
            # if there are conditional parameters this will accommoate an extra feature map 
            elif i==1:
                layers.append(nn.Conv3d(outc+n_cond_features, ndf*int(size), 4, stride=2, padding=1, bias=False))
                layers.append(nn.BatchNorm3d(ndf*int(size)))
                layers.append(nn.LeakyReLU(0.2, inplace=True))
            else:
                layers.append(nn.Conv3d(outc, ndf*int(size), 4, stride=2, padding=1, bias=False))
                layers.append(nn.BatchNorm3d(ndf*int(size)))
                layers.append(nn.LeakyReLU(0.2, inplace=True))
            
            outc = ndf*int(size)
        
        if endact_disc == 'sigmoid':
            layers.append(nn.Conv3d(outc, 1, 4, stride=1, padding=0, bias=False))
            layers.append(nn.Sigmoid())
        elif endact_disc == 'linear':
            layers.append(nn.Linear(outc*4*4*4, 1))
        self.main = nn.Sequential(*layers)
        self.first = nn.Sequential(*first_layer)

    def forward(self, input, zfeatures=None):
        if input.is_cuda and self.ngpu > 1:
            output1 = nn.parallel.data_parallel(self.first, input, range(self.ngpu))
            if zfeatures is not None:
                output1 = torch.cat((output1, zfeatures), 1)
            output = nn.parallel.data_parallel(self.main, output1, range(self.ngpu))
            
        else:  
            output1 = self.first(input)
            output = self.main(output1)

        return output.view(-1, 1).squeeze(1)
