# Andrew Miller <amiller@cs.ucf.edu> 2011
#
# BlockPlayer - 3D model reconstruction using the Lattice-First algorithm
# See: 
#    "Interactive 3D Model Acquisition and Tracking of Building Block Structures"
#    Andrew Miller, Brandyn White, Emiko Charbonneau, Zach Kanzler, and Joseph J. LaViola Jr.
#    IEEE VR 2012, IEEE TVGC 2012
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import numpy as np
import preprocess
import opencl
import lattice
import config
import os
import dataset
import stencil


from ctypes import POINTER as PTR, c_byte, c_size_t, c_float
speedup_ctypes = np.ctypeslib.load_library('speedup_ctypes.so',
                                           os.path.dirname(__file__))
speedup_ctypes.histogram.argtypes = [PTR(c_byte), PTR(c_float), PTR(c_float),
                                     c_size_t, c_size_t, c_size_t, c_size_t]


def initialize():
    b_width = [config.bounds[1][i]-config.bounds[0][i]
               for i in range(3)]

    global occ, vac, color, color_count, previous_estimate, good_alignment
    occ = np.zeros(b_width)>0
    vac = np.zeros(b_width)>0
    color = np.zeros((b_width[0], b_width[1], b_width[2], 3),'u1')
    color_count = np.zeros(b_width,'i')
    good_alignment = False
    previous_estimate = None


if not 'previous_estimate' in globals():
    previous_estimate=occ=vac=occ_stencil=vac_stencil=color=color_count=None
    good_alignment=None
    initialize()


def gt2grid(gtstr, chars='*rR'):
    g = np.array(map(lambda _: map(lambda __: tuple(__), _), eval(gtstr)))
    g = np.rollaxis(g,1)
    res = (g==chars[0])
    for c in chars[1:]:
        res = np.logical_or(res, g==c)
    return np.ascontiguousarray(res)


def grid2gt(occ):
    m = np.choose(occ, (' ', '*'))
    layers = [[''.join(_) for _ in m[:,i,:]]
              for i in range(m.shape[1])]
    import pprint
    return pprint.pformat(layers)


def initialize_with_groundtruth(GT):
    initialize()
    global occ, vac
    occ[:,:] = GT
    vac[:,:] = ~GT


def window_correction(occA, vacA, occB, vacB):
    """ Finds the translation/rotation components that align B with A,
    minimizing an objective function between them. The objective function
    rewards occA and occB cells that agree, and penalizes ones that disagree.
    Weighted contributions:
       occA && occB: -p/4
       occA && vacB or vacA && occB: +p
       other: 0

    Params:
        occA, vacA: boolean 3-d array (the old one)
        occB, vacB: boolean 3-d array (the new one)
    Returns:
        (x,_,z),err:
            x,_,z are corrective translations from B to A, such that
                  B = np.roll(B,x,0)
                  B = np.roll(B,z,2)
                would modify B so that it lines up optimally with A.
            err is the value of the objective function minimized by (x,_,z)
    """
    occR = dict([(r, np.swapaxes(np.rot90(np.swapaxes(occB,1,2), r), 1,2))
                 for r in (0,1,2,3)])
    vacR = dict([(r, np.swapaxes(np.rot90(np.swapaxes(vacB,1,2), r), 1,2))
                 for r in (0,1,2,3)])
    #print [occ.shape for occ in occR.values()]

    def error(t):
        occB = occR[t[3]]
        vacB = vacR[t[3]]
        nv = np.roll(occB, t[0], 0)
        nv = np.roll(nv, t[2], 2)
        nc = np.roll(vacB, t[0], 0)
        nc = np.roll(nc, t[2], 2)
        return np.sum(np.minimum(nc,occA) + np.minimum(nv,vacA) -
                      np.minimum(nv,occA)/4.)

    t = [(x,y,z,r)
         for x in [0,-1,1,-2,2]
         for y in [0]
         for z in [0,-1,1,-2,2]
         for r in [0]]

    vals = [error(_) for _ in t]
    #print vals
    return t[np.argmin(vals)], np.min(vals)


def xcorr_correction(A, B):
    """
    Find the best fit parameters between two voxel grids (e.g. a ground truth
    and an output) using convolution
    Params:
        A: boolean grid of output
        B: boolean grid of ground truth
    """
    def convolve(p1, p2):
        import scipy.signal
        #cc = scipy.signal.correlate2d(p1[1],p2[1],'same')
        cc = scipy.signal.fftconvolve(p1,p2[::-1,::-1])
        return cc

    def best(cc):
        ind = np.argmax(cc)
        x,z = np.unravel_index(ind, cc.shape)
        x -= cc.shape[0]/2
        z -= cc.shape[0]/2
        return x,z,cc.max()

    # Try all four rotations
    global convs
    sA = [A.sum(i) for i in range(3)]
    sB = [B.sum(i) for i in range(3)]

    convs = [convolve(sA[1], np.rot90(sB[1], r)) for r in range(4)]

    cc = [best(_) for _ in convs]
    r = np.argmax([_[2] for _ in cc])
    x,z,_ = cc[r]

    B = apply_correction(B, x, z, r)
    (bx,_,bz,br),e = window_correction(A,A&0,B,~B)
    B = apply_correction(B, bx, bz, br)

    err = float((A&~B).sum()+(B&~A).sum()) / B.sum()
    return A, B, err, (x,z,r), (bx,bz,br)


def show_votegrid(vg, color=(1,0,0), opacity=1):
    from enthought.mayavi import mlab
    mlab.clf()
    x,y,z = np.nonzero(vg>0)
    if not color is None:
        mlab.points3d(x,y,z,
                      opacity=opacity,
                      color=color,
                      scale_factor=1, scale_mode='none', mode='cube')
    else:
        mlab.points3d(x,y,z,vg[vg>0],
                      opacity=opacity,
                      scale_factor=1, scale_mode='none', mode='cube')

    gridmin, gridmax = config.bounds
    X,Y,Z = np.array(gridmax)-gridmin
    #mlab.axes(extent=[0,0,0,X,Y,Z])
    mlab.draw()


def has_previous_estimate():
    global previous_estimate
    return not previous_estimate is None


def apply_correction(grid, bx,bz,rot):
    return np.roll(np.roll(\
            np.swapaxes(np.rot90(np.swapaxes(grid,1,2),rot),1,2),
        bx, 0), bz, 2)


def center(R_aligned, occ_new, vac_new):
    bx,_,bz = [config.GRIDRAD-int(np.round(_.mean()))
               for _ in occ_new.nonzero()]
    occ_new = apply_correction(occ_new, bx, bz, 0)
    vac_new = apply_correction(vac_new, bx, bz, 0)
    R_correct = R_aligned.copy()
    R_correct[0,3] += bx*config.LW
    R_correct[2,3] += bz*config.LW
    return R_correct, occ_new, vac_new


def nearest(R_previous, R_aligned):
    # Find the nearest rotation
    import expmap
    M = [expmap.axis2rot(np.array([0,-i*np.pi/2,0])) for i in range(4)]
    rs = [np.dot(m, R_aligned[:3,:3]) for m in M]
    global dots
    dots = [np.dot(r[0,:3], R_previous[0,:3]) for r in rs]
    rot = np.argmax(dots)
    R_correct = R_aligned.copy()
    R_correct[:3,:3] = rs[rot]
    R_correct[:3,3] = np.dot(M[rot], R_correct[:3,3])

    # Find the nearest translation
    bx = int(np.round((R_previous[0,3]-R_correct[0,3])/config.LW))
    bz = int(np.round((R_previous[2,3]-R_correct[2,3])/config.LW))

    R_correct[0,3] += config.LW * bx
    R_correct[2,3] += config.LW * bz
    return R_correct, (bx,bz,rot)


def align_with_previous(R_aligned, occ_new, vac_new):
    assert R_aligned.dtype == np.float32
    assert R_aligned.shape == (4,4)

    global previous_estimate
    occ = previous_estimate['occ']
    vac = previous_estimate['vac']
    R_previous = previous_estimate['R_correct']

    global R_correct
    R_correct, c = nearest(R_previous, R_aligned)
    occ_new = apply_correction(occ_new, *c)
    vac_new = apply_correction(vac_new, *c)

    (bx,_,bz,rot),err = window_correction(occ, vac, occ_new, vac_new)
    #print 'err: ', err

    if (bx,bz) != (0,0):
        R_correct[0,3] += bx*config.LW
        R_correct[2,3] += bz*config.LW
        occ_new = apply_correction(occ_new, bx, bz, rot)
        vac_new = apply_correction(vac_new, bx, bz, rot)
        print 'slip: %d %d %d' % (bx, bz, rot)

    return R_correct, occ_new, vac_new


def stencil_carve(depth, rect, R_correct, occ, vac, rgb=None):
    global previous_estimate
    if not previous_estimate is None:
        occ_old = previous_estimate['occ']
        cands = occ_old | occ
    else:
        cands = occ
    global b_occ, b_vac, b_total
    b_occ, b_vac, b_total = stencil.stencil_carve(depth, R_correct,
                                                  cands, rgb, rect)

    global occ_stencil, vac_stencil
    occ_stencil = (b_occ/(b_total+1.)>0.9) & (b_total>30)
    vac_stencil = (b_vac/(b_total+1.)>0.6) & (b_total>30)

    global good_alignment
    good_alignment = float(b_occ.sum())/b_total.sum()

    return occ_stencil, vac_stencil


def merge_with_previous(occ_, vac_, occ_stencil, vac_stencil, color_=None):
    # Only allow 'uncarving' of elements attached to known blocks
    import scipy.ndimage
    global occ, vac, color, color_count
    cmask = scipy.ndimage.binary_dilation(occ)

    vac |= vac_
    vac[occ_stencil] = 0
    if occ.sum() > 0:
        occ_ &= cmask
    occ_ &= occ_stencil

    if not color_ is None:
        colormask = occ_stencil&(stencil.b_occ>color_count)
        color[colormask,:] = color_[colormask,:]
        color_count[colormask] = stencil.b_occ[colormask]
        #print np.sum(colormask)
    occ |= occ_
    occ[vac] = 0
    color_count[~occ] = 0


def update_previous_estimate(R_correct):
    global previous_estimate
    previous_estimate = dict(vac=vac,
                             occ=occ,
                             R_correct=R_correct,
                             color=color,
                             color_count=color_count)
