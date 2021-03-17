"""
Function for the detection of sleep boundaries, here defined as the total sleep opportunity window.

Yiorgos Christakis
Pfizer DMTI 2021
"""
from numpy import pad, min, max, percentile, zeros, bool_

from skimu.utility import rolling_mean, rolling_median
from skimu.sleep.utility import *


def get_total_sleep_opportunity(
        fs, time, accel, wear_starts, wear_stops, min_rest_block, max_act_break,
        min_angle_thresh, max_angle_thresh
):
    # samples in 5 seconds
    n5 = int(5 * fs)
    # compute the rolling median for 5s windows
    acc_rmd = rolling_median(accel, n5, skip=1, pad=False, axis=0)

    # compute the z-angle
    z = compute_z_angle(acc_rmd)

    # rolling 5s mean with non-overlapping windows for the z-angle
    z_rm = rolling_mean(z, n5, n5)

    # the angle differences
    dz_rm = compute_absolute_difference(z_rm)

    # rolling 5 minute median. 12 windows per minute * 5 minutes
    dz_rm_rmd = rolling_median(dz_rm, 12 * 5, skip=1, pad=False)

    # compute the TSO threshold
    tso_thresh = compute_tso_threshold(dz_rm_rmd, min_td=min_angle_thresh, max_td=max_angle_thresh)

    # create the TSO mask (1 -> sleep opportunity, only happends during wear)
    tso = zeros(dz_rm_rmd.size, dtype=bool_)
    for strt, stp in zip(wear_starts / n5, wear_stops / n5):  # scale by 5s blocks
        tso[strt:stp] = True

    # apply the threshold
    tso &= dz_rm_rmd < tso_thresh  # now only blocks where there is no movement, and wear are left

    # drop rest blocks less than minimum allowed rest length
    tso = drop_min_blocks(tso, 12 * min_rest_block, drop_value=1, replace_value=0)
    # drop active blocks less than maximum allowed active length
    tso = drop_min_blocks(tso, 12 * max_act_break, drop_value=0, replace_value=1)

    # get the indices of the longest bout
    arg_start, arg_end = arg_longest_bout(tso, 1)

    # account for left justified windows - times need to be bumped up by half a window
    arg_start += 30  # 12 * 5 / 2
    arg_end += 30

    # get the timestamps of the longest bout
    if arg_start is not None:
        start, end = time[arg_start * n5], time[arg_end * n5]
    else:
        start = end = None

    return start, end, arg_start, arg_end


def detect_tso(
        acc, t, fs, temp, min_rest_block, max_act_break, min_angle_thresh,
        max_angle_thresh, move_thresh, temp_thresh
):
    """
    Computes the total sleep opportunity window bounds.

    Parameters
    ----------
    acc : array
        Tri-axial accelerometer data.
    t : array
        Timestamp array.
    fs : float
        Sampling frequency.
    temp : array
        Temperature data.
    min_rest_block : int
        Minimum number of minutes required to consider a rest period valid.
    max_act_break : int
        Maximum number of minutes of an activity period that doesn't interrupt a rest period.
    min_angle_thresh : float
        Minimum dz-angle threshold.
    max_angle_thresh : float
        Maximum dz-angle threshold.
    move_thresh : float
        Movement-based non-wear threshold value. Boolean False negates use.
    temp_thresh : float
        Temperature-based non-wear threshold value.

    Returns
    -------
    tso : tuple
        First and last timestamps of the tso window. First and last indices of tso window.

    """
    # compute rolling 5s median only 1 time
    rmd = rolling_median(acc, int(fs * 5), skip=1, pad=False, axis=0)

    # compute z-angle
    z = compute_z_angle(rmd)

    # rolling 5s mean (non-overlapping windows)
    mnz = rolling_mean(z, int(fs * 5), int(fs * 5))

    # compute dz-angle
    dmnz = compute_absolute_difference(mnz)

    # rolling 5m median. 12 windows per minute (5s windows) * 5 minutes
    rmd_dmnz = rolling_median(dmnz, 12 * 5, skip=1, pad=False)

    # compute threshold
    td = compute_tso_threshold(rmd_dmnz, min_td=min_angle_thresh, max_td=max_angle_thresh)

    # apply threshold
    rmd_dmnz[rmd_dmnz < td] = 0
    rmd_dmnz[rmd_dmnz >= td] = 1

    # compute non-wear
    move_mask = detect_nonwear_mvmt(rmd, fs, move_thresh) if move_thresh else None
    temp_mask = detect_nonwear_temp(temp, fs, temp_thresh) if temp is not None else None

    # apply movement-based non-wear mask
    if move_mask is not None:
        move_mask = pad(
            move_mask,
            (0, len(rmd_dmnz) - len(move_mask)),
            mode="constant",
            constant_values=1,
        )
        rmd_dmnz[move_mask] = 1

    # apply temperature-based non-wear mask
    if temp_mask is not None:
        temp_mask = pad(
            temp_mask,
            (0, len(rmd_dmnz) - len(temp_mask)),
            mode="constant",
            constant_values=1,
        )
        rmd_dmnz[temp_mask] = 1

    # drop rest blocks less than minimum allowed rest length
    rmd_dmnz = drop_min_blocks(rmd_dmnz, 12 * min_rest_block, drop_value=0, replace_value=1)

    # drop active blocks less than maximum allowed active length
    rmd_dmnz = drop_min_blocks(rmd_dmnz, 12 * max_act_break, drop_value=1, replace_value=0)

    # get indices of longest bout
    arg_start, arg_end = arg_longest_bout(rmd_dmnz, 0)

    # account for left-justified windows - times need to be bumped up by half a window
    arg_start += 30
    arg_end += 30  # 12 * 5 / 2

    # get timestamps of longest bout
    if arg_start is not None:
        start, end = t[arg_start * int(5 * fs)], t[arg_end * int(5 * fs)]
    else:
        start, end = None, None

    return start, end, arg_start, arg_end


def compute_tso_threshold(arr, min_td=0.1, max_td=0.5):
    """
    Computes the daily threshold value separating rest periods from active periods for the TSO detection algorithm.

    Parameters
    ----------
    arr : array
        Array of the absolute difference of the z-angle.
    min_td : float
        Minimum acceptable threshold value.
    max_td : float
        Maximum acceptable threshold value.

    Returns
    -------
    td : float

    """
    td = min((max((percentile(arr, 10) * 15.0, min_td)), max_td))
    return td
