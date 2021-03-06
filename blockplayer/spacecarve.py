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
import config
import speedup_cy


def depth_inds(modelmat, X, Y, Z):
    gridmin, gridmax = config.bounds

    bg = config.bg
    mat = np.linalg.inv(np.dot(np.dot(modelmat,
                               bg['Ktable']),
                               bg['KK']))
    x = X*mat[0,0] + Y*mat[0,1] + Z*mat[0,2] + mat[0,3]
    y = X*mat[1,0] + Y*mat[1,1] + Z*mat[1,2] + mat[1,3]
    z = X*mat[2,0] + Y*mat[2,1] + Z*mat[2,2] + mat[2,3]
    w = X*mat[3,0] + Y*mat[3,1] + Z*mat[3,2] + mat[3,3]

    return x/w, y/w, z/w


def carve(depth, modelmat):

    if 1:
        gridmin, gridmax = config.bounds
        gridmin = np.array(gridmin).astype('i')
        gridmax = np.array(gridmax).astype('i')
        length = np.sqrt((config.LW**2+
                          config.LH**2+
                          config.LW**2))/2
        vac = np.zeros((gridmax[0]-gridmin[0],
                        gridmax[1]-gridmin[1],
                        gridmax[2]-gridmin[2]), 'u1')
        mat = config.bg['KK']
        modelmat = np.linalg.inv(np.dot(np.dot(modelmat,
                                               config.bg['Ktable']),
                                        config.bg['KK']))
        modelmat = np.ascontiguousarray(modelmat)
        speedup_cy.spacecarve(depth, vac, modelmat, mat,
                              gridmin, gridmax,
                              config.LW, config.LH, length)
    if 0:
        vac_numpy = carve_numpy(depth, modelmat)
        assert np.all(vac == vac_numpy)
    return vac


def carve_numpy(depth, modelmat):
    """
    Sample the depth image at the center point of each voxel. Mark as 'vacant
    all the voxels that we can see right through.

    Output:
        vac: vacancy grid [gridmin:gridmax]
        occ: unused
    """
    global x, y, d

    gridmin, gridmax = config.bounds

    # Consider the center points of each candidate voxel
    X,Y,Z = np.mgrid[gridmin[0]:gridmax[0],
                     gridmin[1]:gridmax[1],
                     gridmin[2]:gridmax[2]]+0.5

    X *= config.LW
    Y *= config.LH
    Z *= config.LW

    # Find the reference depth for each voxel, and the sampled depth
    x,y,dref = depth_inds(modelmat, X,Y,Z)
    depth_ = depth.astype('f')
    #depth_[depth==2047] = -np.inf

    import scipy.ndimage
    warn = np.seterr(divide='ignore')
    d = 1000./scipy.ndimage.map_coordinates(depth_, (y,x), order=0,
                                            prefilter=False,
                                            cval=-np.inf)

    # Project to metric depth

    mat = config.bg['KK']
    z = x*mat[2,0] + y*mat[2,1] + d*mat[2,2] + mat[2,3]
    w = x*mat[3,0] + y*mat[3,1] + d*mat[3,2] + mat[3,3]
    dmet = z/w

    z = x*mat[2,0] + y*mat[2,1] + dref*mat[2,2] + mat[2,3]
    w = x*mat[3,0] + y*mat[3,1] + dref*mat[3,2] + mat[3,3]
    drefmet = z/w

    length = np.sqrt((config.LW**2+
                      config.LH**2+
                      config.LW**2))/2
    np.seterr(divide=warn['divide'])

    global vac
    vac = (d>0)&(dmet<drefmet-length)
    return vac
