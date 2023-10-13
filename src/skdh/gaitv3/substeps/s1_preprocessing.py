"""
Gait bout acceleration pre-processing functions.

Lukas Adamowicz
Copyright (c) 2023, Pfizer Inc. All rights reserved
"""
from numpy import mean, std, median, argmax, sign, abs, argsort, corrcoef, diff, array
from scipy.signal import detrend, butter, sosfiltfilt, find_peaks

from skdh.base import BaseProcess
from skdh.utility import correct_accelerometer_orientation
from skdh.gait.gait_endpoints import gait_endpoints


class V1Preprocess(BaseProcess):
    """
    Preprocess acceleration data for gait using the original/V1 method.

    Parameters
    ----------
    correct_orientation : bool, optional
        Correct the accelerometer orientation if it is slightly mis-aligned
        with the anatomical axes. Default is True.
    filter_cutoff : float, optional
        Low-pass filter cutoff in Hz. Default is 20.0
    filter_order : int, optional
        Low-pass filter order. Default is 4.
    """
    def __init__(
            self,
            correct_orientation=True,
            filter_cutoff=20.0,
            filter_order=4
    ):
        super().__init__(
            correct_orientation=correct_orientation,
            filter_cutoff=filter_cutoff,
            filter_order=filter_order,
        )

        self.corr_orient = correct_orientation
        self.filter_cutoff = filter_cutoff
        self.filter_order = filter_order

    def predict(self, time=None, accel=None, *, fs=None, **kwargs):
        super().predict(
            expect_days=False,
            expect_wear=False,
            **kwargs,
        )
        # calculate fs if we need to
        fs = 1 / mean(diff(time)) if fs is not None else fs

        # figure out the vertical axis
        acc_mean = mean(accel, axis=0)
        v_axis = argmax(abs(acc_mean))
        va_sign = sign(acc_mean[v_axis])  # sign of the vertical acceleration

        # correct accelerometer orientation if set to do so
        if self.corr_orient:
            # determine ap axis
            ac = gait_endpoints._autocovariancefn(
                accel, min(accel.shape[0] - 1, 1000), biased=True, axis=0
            )
            ap_axis = argsort(corrcoef(ac.T)[v_axis])[-2]

            accel = correct_accelerometer_orientation(accel, v_axis=v_axis, ap_axis=ap_axis)

        vert_accel = detrend(accel[:, v_axis])  # detrend just in case

        # low-pass filter if we can
        if 0 < (2 * self.filter_cutoff / fs) < 1:
            sos = butter(self.filter_order, 2 * self.filter_cutoff / fs, btype='low', output='sos')
            filt_vert_accel = sosfiltfilt(sos, vert_accel)
        else:
            # multiply by 1 to ensure a copy and not a view
            filt_vert_accel = vert_accel * 1

        kwargs.update(
            {
                self._time: time,
                self._acc: accel,
                "fs": fs,
                "v_axis": v_axis,
                "va_sign": va_sign,
                "vert_accel": filt_vert_accel,
            }
        )

        return (kwargs, None) if self._in_pipeline else kwargs


class V2Preprocess(BaseProcess):
    """
    Preprocess acceleration data for gait using the newer/V2 method.

    Parameters
    ----------
    correct_orientation : bool, optional
        Correct the accelerometer orientation if it is slightly mis-aligned
        with the anatomical axes. Default is True.
    filter_cutoff : float, optional
        Low-pass filter cutoff in Hz. Default is 20.0
    filter_order : int, optional
        Low-pass filter order. Default is 4.
    """
    def __init__(self, correct_orientation=True, filter_cutoff=20.0, filter_order=4):
        super().__init__(
            correct_orientation=correct_orientation,
            filter_cutoff=filter_cutoff,
            filter_order=filter_order,
        )

        self.corr_orient = correct_orientation
        self.filter_cutoff = filter_cutoff
        self.filter_order = filter_order

    @staticmethod
    def get_ap_axis_sign(fs, accel, ap_axis):
        """
        Estimate the sign of the AP axis

        Parameters
        ----------
        fs : float
            Sampling frequency in Hz.
        accel : numpy.ndarray
        ap_axis : int
            Anterior-Posterior axis

        Returns
        -------
        ap_axis_sign : {-1, 1}
            Sign of the AP axis.
        """
        sos = butter(4, [2 * 0.25 / fs, 2 * 7.0 / fs], output='sos', btype='band')
        ap_acc_f = sosfiltfilt(sos, accel[:, ap_axis])

        mx, mx_meta = find_peaks(ap_acc_f, prominence=0.05)
        med_prom = median(mx_meta['prominences'])
        mask = mx_meta['prominences'] > (0.75 * med_prom)

        left_med = median(mx[mask] - mx_meta['left_bases'][mask])
        right_med = median(mx_meta['right_bases'][mask] - mx[mask])

        sign = -1 if (left_med < right_med) else 1

        return sign

    def predict(self, time=None, accel=None, *, fs=None, v_axis=None, ap_axis=None, **kwargs):
        """
        predict(time, accel, *, fs=None, v_axis=None, ap_axis=None)

        Parameters
        ----------
        time : numpy.ndarray
            (N, ) array of unix timestamps, in seconds
        accel : numpy.ndarray
            (N, 3) array of accelerations measured by a centrally mounted lumbar
            inertial measurement device, in units of 'g'.
        fs : float, optional
            Sampling frequency in Hz of the accelerometer data. If not provided,
            will be computed form the timestamps.
        v_axis : {None, 0, 1, 2}, optional
            Index of the vertical axis in the acceleration data. Default is None.
            If None, will be estimated from the acceleration data.
        ap_axis : {None, 0, 1, 2}, optional
            Index of the Anterior-Posterior axis in the acceleration data.
            Default is None. If None, will be estimated from the acceleration data.

        Returns
        -------

        """
        # calculate fs if we need to
        fs = 1 / mean(diff(time)) if fs is not None else fs

        # estimate accelerometer axes if necessary
        acc_mean = mean(accel, axis=0)
        if v_axis is None:
            v_axis = argmax(abs(acc_mean))

        # always compute the sign
        v_axis_sign = sign(acc_mean[v_axis])

        if ap_axis is None:
            sos = butter(4, 2 * 3.0 / fs, output='sos')
            acc_f = sosfiltfilt(sos, accel, axis=0)

            ac = gait_endpoints._autocovariancefn(
                acc_f,
                min(accel.shape[0] - 1, int(10 * fs)),
                biased=True,
                axis=0
            )

            ap_axis = argsort(corrcoef(ac.T)[v_axis])[-2]

        # always compute the sign
        ap_axis_sign = self.get_ap_axis_sign(fs, accel, ap_axis)

        if self.corr_orient:
            accel = correct_accelerometer_orientation(accel, v_axis=v_axis, ap_axis=ap_axis)

        # filter
        sos = butter(self.filter_order, 2 * self.filter_cutoff / fs, output='sos', btype='low')
        ap_acc_f = sosfiltfilt(sos, accel[:, ap_axis])

        # detrend
        ap_acc_f = detrend(ap_acc_f)

        # estimate step frequency
        sos = butter(4, 2 * 10.0 / fs, output='sos')
        sf_acc_f = sosfiltfilt(sos, accel, axis=0)

        ac = gait_endpoints._autocovariancefn(
            sf_acc_f,
            min(sf_acc_f.shape[0] - 1, int(4 * fs)),
            biased=True,
            axis=0
        )

        factor = 1.0
        pks = array([])
        while factor > 0.5 and pks.size == 0:
            pks, _ = find_peaks(ac[:, ap_axis], prominence=factor * std(ac[:, ap_axis]))
            factor -= 0.05

        idx = argsort(ac[pks, ap_axis])[-1]

        step_samples = pks[idx]
        mean_step_freq = 1 / (step_samples / fs)

        kwargs.update(
            {
                self._time: time,
                self._acc: accel,
                'fs': fs,
                'v_axis': v_axis,
                'v_axis_sign': v_axis_sign,
                'ap_axis': ap_axis,
                'ap_axis_sign': ap_axis_sign,
                'mean_step_freq': mean_step_freq,
                'ap_accel': ap_acc_f,
            }
        )

        return (kwargs, None) if self._in_pipeline else kwargs

