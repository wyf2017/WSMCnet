#!/usr/bin/env python
# -*- coding: UTF-8 -*-

import os
import shutil
import time
#import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import skimage
from dataloader import dataloader_by_name
from models import model_by_name
from meters import CreateMeter, StereoMeters

import logging
logger = logging.getLogger(__name__)


def get_model(args):

    # create model
    model = model_by_name(args.arch, args.maxdisp)
    if args.cuda: # carry model to cuda
        model = nn.DataParallel(model)
        model.cuda()
    
    # log num_model_parameters
    num_model_parameters = sum([p.data.nelement() for p in model.parameters()])
    msg = 'Modules of model: \n{0} \n\n Number of model parameters: {1}'.format(
            str(model), num_model_parameters)
    logger.info(msg)
    
    return model

def lr_adjust(optimizer, epoch, args):
    
    lr = args.lr
    depoch = epoch - args.lr_epoch0
    if(depoch >= 0):
   
        count = 1 + ((epoch-args.lr_epoch0)//args.lr_stride)
        lr *= args.lr_delay**count
        for param_group in optimizer.param_groups:
            param_group['lr'] = lr

        #lr_curr = [prm_grp['lr'] for prm_grp in optimizer.param_groups]
        #lr_decay = max(lr_curr)/lr
        #for param_group in optimizer.param_groups:
        #    param_group['lr'] *= lr_decay

    return lr

def train_val(args):

    #---------preparation---------------------------------------------------------------------------
    TrainImgLoader = dataloader_by_name(
                        args.datas_train, args.dir_datas_train, args.bn, training=True, 
                        crop_size=args.crop_size, n=3)
    ValImgLoader = dataloader_by_name(
                        args.datas_val, args.dir_datas_val, args.bn, training=False, 
                        crop_size=[0, 0], n=3)
    model = get_model(args)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr, betas=args.beta)

    # load pretrained weight
    if os.path.exists(str(args.loadmodel)):
        
        state_dict = torch.load(args.loadmodel)
        model.load_state_dict(state_dict['state_dict'])
        msg = 'Load pretrained weight successfully ! \nLoaded weight file: %s\n' % str(args.loadmodel)
        logger.info(msg)

    elif('None'!=str(args.loadmodel).strip()):
        
        msg = 'No available weight ! \nPlease check weight file: %s\n\n' % str(args.loadmodel)
        logger.warning(msg)

    #---------progess of last interrupt------------------------------------------------------------------
    dir_save = args.dir_save # dirpath for saving weight
    if(not os.path.isdir(dir_save)):
        os.makedirs(dir_save)
    path_checkpoint = os.path.join(dir_save, 'checkpoint.pkl')
    path_training_info = os.path.join(dir_save, 'training_info.pkl')
    
    # load checkpoint if it exist
    epoch0_train, epoch0_val = 0, 0
    meters_all = StereoMeters()
    meters_epoch = {}
    
    if(os.path.isfile(path_training_info)):
        
        info_dict = torch.load(path_training_info)
        meters_all.extend(info_dict)
        epochs_val = meters_all.data['epoch']
        epoch0_val = len(epochs_val)
    
    if(os.path.isfile(path_checkpoint)):
        
        data = torch.load(path_checkpoint)
        epoch0_train = data['epoch']
        meters_epoch['train_loss'] = data['train_loss']
        model.load_state_dict(data['state_dict']) # recover model state
        optimizer.load_state_dict(data['state_dict_optim']) # recover optimizer state
        torch.random.set_rng_state(data['random_state']) # recover random state

    # log msg
    msg = 'Trained %d epoch | Valed %d epoch \n' % (epoch0_train, epoch0_val)
    logger.info(msg)
    
    #---------start training-----------------------------------------------------------------------
    start_full_time = time.time()
    for epoch in range(epoch0_val + 1, args.epochs+1):
        
        lr = lr_adjust(optimizer, epoch, args)
        msg = 'This is %d-th epoch, lr=%f' % (epoch, lr)
        logger.info(msg)
        meters_epoch.update({'epoch': epoch})

        #---------Train-----------------------------------------------------------------------
        if(epoch > epoch0_train):
            
            dataloader = TrainImgLoader
            meters = epoch_train(epoch, args.nloop, dataloader, model, optimizer, 
                                use_cuda=args.cuda, freq_optim=args.freq_optim, freq_print=args.freq_print)
            meters_epoch.update(meters) # record train meters in a epoch
            
            # log msg
            full_time_hour = (time.time() - start_full_time)/3600
            msg = 'Train %d | full training time = %.2f HR \n' % (epoch, full_time_hour)
            logger.info(msg)

            # save checkpoint
            path_weight_epoch = os.path.join(dir_save, 'weight_%d.pkl' % epoch)
            torch.save({'state_dict': model.state_dict()}, path_weight_epoch)
            state_dict = {'epoch': epoch,
                          'train_loss': meters['train_loss'], 
                          'state_dict': model.state_dict(),
                          'state_dict_optim': optimizer.state_dict(),
                          'random_state': torch.random.get_rng_state(), 
                          }

            torch.save(state_dict, path_checkpoint+'.tm~')
            shutil.move(path_checkpoint+'.tm~', path_checkpoint)

        #---------Val-----------------------------------------------------------------------
        if(True):
            
            dataloader = ValImgLoader
            meters = epoch_val(epoch, dataloader, model, args.cuda, args.freq_print, dir_save)
            meters_epoch.update(meters) # record Val meters in a epoch

            # log msg
            full_time_hour = (time.time() - start_full_time)/3600
            msg = 'Val %d | full training time = %.2f HR \n' % (epoch, full_time_hour)
            logger.info(msg)

        # record train meters in a epoch
        meters_all.append(meters_epoch)

        # save training information
        torch.save(meters_all.data, path_training_info+'.tm~')
        shutil.move(path_training_info+'.tm~', path_training_info)

def val(args):

    #---------preparation---------------------------------------------------------------------------
    ValImgLoader = dataloader_by_name(
                        args.datas_val, args.dir_datas_val, args.bn, training=False, 
                        crop_size=[0, 0], n=3)
    model = get_model(args)

    # load pretrained weight
    if os.path.exists(str(args.loadmodel)):
        
        state_dict = torch.load(args.loadmodel)
        model.load_state_dict(state_dict['state_dict'])
        msg = 'Load pretrained weight successfully ! \nLoaded weight file: %s\n' % str(args.loadmodel)
        logger.info(msg)

    elif('None'!=str(args.loadmodel).strip()):
        
        msg = 'No available weight ! \nPlease check weight file: %s\n\n' % str(args.loadmodel)
        logger.warning(msg)

    #---------Val-----------------------------------------------------------------------
    start_full_time = time.time()
    dataloader = ValImgLoader
    meters = epoch_val(0, dataloader, model, args.cuda, args.freq_print)

    # log msg
    full_time_hour = (time.time() - start_full_time)/3600
    msg = 'Full Val time = %.2f HR \n' % (full_time_hour)
    logger.info(msg)
    msg = '\n' + '\n'.join(['%s: %.3f'%(k, v) for k, v in meters.items()])
    logger.info(msg)

def submission(args):

    #---------preparation---------------------------------------------------------------------------
    ValImgLoader = dataloader_by_name(
                        args.datas_val, args.dir_datas_val, 1, training=False, 
                        crop_size=None, n=2)
    model = get_model(args)

    # load pretrained weight
    if os.path.exists(str(args.loadmodel)):

        state_dict = torch.load(args.loadmodel)
        model.load_state_dict(state_dict['state_dict'])
        msg = 'Load pretrained weight successfully ! \nLoaded weight file: %s\n' % str(args.loadmodel)
        logger.info(msg)

    elif('None'!=str(args.loadmodel).strip()):
        
        msg = 'No available weight ! \nPlease check weight file: %s\n\n' % str(args.loadmodel)
        logger.warning(msg)

    #---------Submission-----------------------------------------------------------------------
    dir_save = args.dir_save
    if(not os.path.isdir(dir_save)):
        os.makedirs(dir_save)
    start_full_time = time.time()
    dataloader = ValImgLoader
    meters = epoch_submission(dataloader, model, dir_save, 
                        use_cuda=args.cuda, freq_print=args.freq_print)

    # log msg
    full_time_hour = (time.time() - start_full_time)/3600
    msg = 'Full Submission time = %.2f HR \n' % (full_time_hour)
    logger.info(msg)
    msg = '\n' + '\n'.join(['%s: %.3f'%(k, v) for k, v in meters.items()])
    logger.info(msg)

def epoch_train(epoch, nloop, dataloader, model, optimizer, use_cuda=True, freq_optim=1, freq_print=1):

    torch.cuda.empty_cache()
    # Meters
    #loss = CreateMeter(mtype='smooth', delay=0.99)
    loss = CreateMeter(mtype='avg')
    batch_time = CreateMeter(mtype='avg')
    data_time = CreateMeter(mtype='avg')
    
    # start
    start_time = time.time()
    iter_all = len(dataloader)*nloop
    for i in range(nloop):
        for batch_idx, (filename, imgL, imgR, dispL) in enumerate(dataloader):
            
            bn = imgL.shape[0]
            if use_cuda: # carry data to GPU
                imgL, imgR, dispL = imgL.cuda(), imgR.cuda(), dispL.cuda()
            data_time.update(time.time()-start_time, bn) # record time of loading data

            # train on a batch of sample
            flag_step = (batch_idx % freq_optim == 0) or (iter_all-1 == batch_idx)
            meters = step_train(model, imgL, imgR, dispL, optimizer, flag_step)
            loss.update(meters['loss'], bn) # record train loss
            batch_time.update(time.time()-start_time, bn) # record time of a batch
            
            # log msg
            if(batch_idx % freq_print == 0):
                iter = batch_idx + i*len(dataloader)
                msg = ('Train [{0}|{1:3d}/{2}] | '
                       'Time {batch_time.avg:.3f}({data_time.avg:.3f}) | '
                       'Loss {loss.val:6.3f} ({loss.avg:6.3f}) '
                       ''.format(epoch, iter, iter_all, batch_time=batch_time, 
                                data_time=data_time, loss=loss))
                logger.info(msg)
            
            # reset start_time
            start_time = time.time()
    
    # log msg
    msg = 'Train %d | mean loss = %.3f | mean batch_time = %.3f ' % (
            loss.avg, loss.avg, batch_time.avg)
    logger.info(msg)
    
    # return meters
    meters = {'train_loss': loss.avg }
    return meters

def epoch_val(epoch, dataloader, model, use_cuda=True, freq_print=1, dir_save=None):

    torch.cuda.empty_cache()
    # Meters
    err_epe = CreateMeter(mtype='avg')
    err_3px = CreateMeter(mtype='avg')
    batch_time = CreateMeter(mtype='avg')
    data_time = CreateMeter(mtype='avg')
    
    # start
    start_time = time.time()
    iter_all = len(dataloader)
    idx_select = torch.randint(iter_all, size=(1,)).item()
    dispL_select = None
    for batch_idx, (filename, imgL, imgR, dispL) in enumerate(dataloader):
        
        bn = imgL.shape[0]
        if use_cuda: # carry data to GPU
            imgL, imgR, dispL = imgL.cuda(), imgR.cuda(), dispL.cuda()
        data_time.update(time.time()-start_time, bn) # record data_time

        # validatin on a batch of sample
        meters, dispL_pred = step_val(model, imgL, imgR, dispL)
        err_epe.update(meters['err_epe'], bn) # record err_epe
        err_3px.update(meters['err_3px'], bn) # record err_3px
        batch_time.update(time.time()-start_time, bn) # record batch_time

        # select a batch of sample for visualization
        if (batch_idx == idx_select) and (dir_save is not None):
            dispL_select = {'dispL_pred': dispL_pred.cpu().data, 
                            'dispL_true': dispL.cpu().data}
            path = os.path.join(dir_save, 'dispL_val_%02d_%d.pkl' % (epoch, batch_idx))
            path_tmp = path + '.tm~'
            torch.save(dispL_select, path_tmp)
            shutil.move(path_tmp, path)
        
        # log msg
        if(batch_idx % freq_print == 0):
            iter = batch_idx
            msg = ('Val [{0}|{1:3d}/{2}] | '
                   'Time {batch_time.avg:.3f}({data_time.avg:.3f}) | '
                   'epe {err_epe.val:6.3f} ({err_epe.avg:6.3f}) '
                   '3px {err_3px.val:6.3f} ({err_3px.avg:6.3f}) '
                   ''.format(epoch, iter, iter_all, batch_time=batch_time, 
                            data_time=data_time, err_epe=err_epe, err_3px=err_3px))
            logger.info(msg)
    
        # reset start_time
        start_time = time.time()

    # log msg
    msg = 'Val %d | mean epe = %.3f | mean 3px = %.3f | mean batch_time = %.3f ' % (
            epoch, err_epe.avg, err_3px.avg, batch_time.avg)
    logger.info(msg)
    
    # return meters
    meters = {'val_epe': err_epe.avg, 
              'val_3px': err_3px.avg, 
              'val_time': batch_time.avg}
    return meters

def epoch_submission(dataloader, model, dir_save, use_cuda=True, freq_print=1):

    torch.cuda.empty_cache()
    # Meters
    runtime = CreateMeter(mtype='avg')
    batch_time = CreateMeter(mtype='avg')
    data_time = CreateMeter(mtype='avg')
    
    # start 
    start_time = time.time()
    iter_all = len(dataloader)
    for batch_idx, (filename, imgL, imgR) in enumerate(dataloader):
        
        bn = imgL.shape[0]
        if use_cuda: # carry data to GPU
            imgL, imgR = imgL.cuda(), imgR.cuda()
        data_time.update(time.time()-start_time, bn) # record time of loading data

        # Submission on a batch of sample
        path_save = os.path.join(dir_save, filename[0])
        meters = step_submission(model, imgL, imgR, path_save)
        runtime.update(meters['runtime'], bn) # record runtime
        batch_time.update(time.time()-start_time, bn) # record batch_time

        # log msg
        if(batch_idx % freq_print == 0):
            iter = batch_idx
            msg = ('Submission [{0:3d}/{1}] | '
                   'Time {batch_time.avg:6.3f}({data_time.avg:6.3f}) | '
                   'Runtime {runtime.val:6.3f} ({runtime.avg:6.3f}) '
                   ''.format(iter, iter_all, batch_time=batch_time, 
                            data_time=data_time, runtime=runtime))
            logger.info(msg)
        
        # reset start_time
        start_time = time.time()

    # log msg
    msg = 'Submission | mean Runtime = %.3f | mean batch_time = %.3f' % (
            runtime.avg, batch_time.avg)
    logger.info(msg)
    
    # return meters
    meters = {'runtime': runtime.avg, 
              'batch_time': batch_time.avg,}
    return meters

def step_train(model, imgL, imgR, dispL, optimizer, flag_optim):
    model.train()

    # compute loss
    disps = model(imgL, imgR)
    loss = model.module.compute_cost(disps, dispL)

    # backward
    if(loss > 0):
        loss.backward()

    # update weight
    if(flag_optim):
        optimizer.step()
        optimizer.zero_grad()

    # return
    meters_dict = {'loss': loss.data.item()}
    return meters_dict

def step_val(model, imgL, imgR, dispL):
    model.eval()

    # predict disp
    with torch.no_grad():
        dispL_pred = model(imgL,imgR)
    maxdisp = model.module.maxdisp

    # compute epe and 3px(D1)
    meters_dict = evalutate(dispL_pred, dispL, maxdisp)
    return meters_dict, dispL_pred

def step_submission(model, imgL, imgR, path_save):
    model.eval()

    # pad image
    top_pad = get_pad(imgL.shape[-2], 16)
    left_pad = get_pad(imgL.shape[-1], 16)
    imgL = F.pad(imgL, [left_pad, 0, top_pad, 0])
    imgR = F.pad(imgR, [left_pad, 0, top_pad, 0])

    # predict disp
    start_time = time.time()
    with torch.no_grad():
        dispL_pred = model(imgL, imgR)
    runtime = time.time() - start_time
    
    # save result
    img = dispL_pred.squeeze(0).data.cpu().numpy()
    img = img[top_pad:, left_pad:]
    skimage.io.imsave(path_save, (img*256).astype('uint16'))

    # return
    meters_dict = {'runtime': runtime}
    return meters_dict

def get_pad(num, ceil):
    
    mod = num % ceil
    pad = ceil - mod if mod>0 else 0
    return pad

def evalutate(disp_pred, disp_true, maxdisp):
    
    # select valid pixels
    mask = (disp_true > 0) & (disp_true < maxdisp)
    if len(mask[mask])==0:
       return {'err_epe': 0, 'err_3px': 0}

    # computing end-point-error
    diff = torch.abs(disp_pred[mask] - disp_true[mask])
    err_epe = torch.mean(diff)
    if(type(err_epe) is torch.Tensor):
        err_epe = err_epe.item() 

    # computing 3-px error
    correct = ((diff < 3) | (diff < disp_true[mask]*0.05))
    err_3px = 100*(1.0 - float(len(correct[correct]))/len(mask[mask]))

    # return
    meters_dict = {'err_epe': err_epe, 'err_3px': err_3px}
    return meters_dict

