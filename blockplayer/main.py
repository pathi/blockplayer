import numpy as np
import config
import preprocess
import normals
import opencl
import lattice
import grid
import spacecarve
import stencil
import colors
import occvac
import dataset
import hashalign


def initialize():
    grid.initialize()


def update_frame(depth, rgb=None):
    def from_rect(m,rect):
        (l,t),(r,b) = rect
        return m[t:b,l:r]

    global mask, rect, modelmat

    try:
        (mask,rect) = preprocess.threshold_and_mask(depth,config.bg)
    except IndexError:
        grid.initialize()
        modelmat = None
        return

    # Compute the surface normals
    normals.normals_opencl(depth, mask, rect)

    # Find the lattice orientation and then translation
    global R_oriented, R_aligned, R_correct
    R_oriented = lattice.orientation_opencl()
    R_aligned = lattice.translation_opencl(R_oriented)

    # Use occvac to estimate the voxels from just the current frame
    occ, vac = occvac.carve_opencl()

    # Further carve out the voxels using spacecarve
    warn = np.seterr(invalid='ignore')
    try:
        vac = vac | spacecarve.carve(depth, R_aligned)
    except np.LinAlgError:
        return
    np.seterr(divide=warn['invalid'])

    if 1 and grid.has_previous_estimate() and np.any(grid.occ):
        if 0:
            R_aligned, c = grid.nearest(grid.previous_estimate['R_correct'],
                                        R_aligned)
            occ = occvac.occ = grid.apply_correction(occ, *c)
            vac = occvac.vac = grid.apply_correction(vac, *c)

            # Align the new voxels with the previous estimate
            R_correct, occ, vac = grid.align_with_previous(R_aligned, occ, vac)
        else:
            c,err = hashalign.find_best_alignment(grid.occ, grid.vac, occ, vac,
                                                  R_aligned,
                                        grid.previous_estimate['R_correct'])
            R_correct = hashalign.correction2modelmat(R_aligned, *c)
            grid.R_correct = R_correct
            occ = occvac.occ = hashalign.apply_correction(occ, *c)
            vac = occvac.vac = hashalign.apply_correction(vac, *c)

    elif np.any(occ):
        # If this is the first estimate (bootstrap) then try to center the grid
        R_correct, occ, vac = grid.center(R_aligned, occ, vac)
    else:
        return

    occ_stencil, vac_stencil = grid.stencil_carve(depth, rect,
                                                  R_correct, occ, vac,
                                                  rgb)
    if lattice.is_valid_estimate():
        # Run stencil carve and merge
        color = stencil.RGB if not rgb is None else None
        grid.merge_with_previous(occ, vac, occ_stencil, vac_stencil, color)
    grid.update_previous_estimate(R_correct)
