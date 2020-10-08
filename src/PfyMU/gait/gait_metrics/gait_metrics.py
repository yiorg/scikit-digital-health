"""
Gait event-level and bout-level metric definitions

Lukas Adamowicz
2020, Pfizer DMTI


     IC             FC      IC             FC      IC             FC
     i-1            i-1     i+1            i+1     i+3            i+3
L    |--------------|       |--------------|       |--------------|
R               |--------------|        |--------------|
                i              i        i+2            i+2
                IC             FC       IC             FC

For step/stride starting at IC_i
stride: IC_i+2 - IC_i
stance: FC_i   - IC_i
swing:  IC_i+2 - FC_i   // IC_i+2 - FCof_i+1 :: stride - stance = (IC_i+2 - IC_i) - FC_i + IC_i
step:   IC_i+1 - IC_i
ids:    FC_i-1 - IC_i   // FCof_i - IC_i
tds:    FC_i   - IC_i+1 // FCof_i+1 - IC_i+1
tds:    ids + tds
ss:     IC_i+1 - FC_i-1 // IC_i+1 - FCof_i
ss:     stance - tds = - FC_i-1 + IC_i+1[ + IC_i - IC_i - FC_i + FC_i]

h = signal_range(vpos_IC_i : vpos_IC_i+1)
step length: 2 * sqrt(2 * l * h - h**2)
stride length: step_length_i + step_length_i+1

gait speed: stride_length / stride time
"""
from numpy import zeros, nanmean, mean, nanstd, std, sum, sqrt, nan, nonzero, argmin, abs, round, \
    float_, int_, fft, arange
from scipy.signal import butter, sosfiltfilt, find_peaks


from PfyMU.gait.gait_metrics.base import EventMetric, BoutMetric, basic_asymmetry


__all__ = ['StrideTime', 'StanceTime', 'SwingTime', 'StepTime', 'InitialDoubleSupport',
           'TerminalDoubleSupport', 'DoubleSupport', 'SingleSupport', 'StepLength',
           'StrideLength', 'GaitSpeed', 'Cadence', 'GaitSymmetryIndex', 'IntraStepCovariance',
           'IntraStrideCovariance', 'HarmonicRatioV', 'PhaseCoordinationIndex', 'StepRegularityV',
           'StrideRegularityV', 'AutocorrelationSymmetryV']


def _autocovariancefunction(x, max_lag, biased=False):
    if x.ndim == 1:
        N = x.size
        ac = zeros(max_lag, dtype=float_)
        axis = -1
    elif x.ndim == 2:
        N = x.shape[0]
        ac = zeros((max_lag, x.shape[1]), dtype=float_)
        axis = 0
    else:
        raise ValueError('Too many dimensions (>2) for x')

    for i in range(max_lag):
        ac[i] = sum(
            (x[:N-i] - mean(x[:N-i], axis=axis)) * (x[i:] - mean(x[i:], axis=axis)), axis=axis
        )
        if biased:
            ac[i] /= (N * std(x[:N-i], ddof=1) * std(x[i:], ddof=1))
        else:
            ac[i] /= ((N - i) * std(x[:N-i], ddof=1) * std(x[i:], ddof=1))

    return ac


def _autocovariance_lag(x, lag, biased=False):
    if x.ndim == 1:
        N = x.size
        m1 = mean(x[:N-lag])
        m2 = mean(x[lag:])
        s1 = std(x[:N-lag], ddof=1)
        s2 = std(x[lag:], ddof=1)

        ac = sum((x[:N-lag] - m1) * (x[lag:] - m2))
    elif x.ndim == 2:
        N = x.shape[0]
        m1 = mean(x[:N-lag], axis=0)
        m2 = mean(x[lag:], axis=0)
        s1 = std(x[:N-lag], ddof=1, axis=0)
        s2 = std(x[lag:], ddof=1, axis=0)

        ac = sum((x[:N-lag] - m1) * (x[lag:] - m2), axis=0)
    else:
        raise ValueError('Too many dimensions (>2) for x')
    if biased:
        ac /= (N * s1 * s2)
    else:
        ac /= ((N - lag) * s1 * s2)
    return ac


def _autocovariance(x, i1, i2, i3, biased=False):
    if i3 > x.size:
        return nan

    N = i3 - i1
    m = i2 - i1
    m1, s1 = mean(x[i1:i2]), std(x[i1:i2], ddof=1)
    m2, s2 = mean(x[i2:i3]), std(x[i2:i3], ddof=1)

    ac = sum((x[i1:i2] - m1) * (x[i2:i3] - m2))
    if biased:
        ac /= (N * s1 * s2)
    else:
        ac /= ((N - m) * s1 * s2)

    return ac


# ===========================================================
#     GAIT EVENT-LEVEL METRICS
# ===========================================================
class StrideTime(EventMetric):
    """
    Stride time is the time to complete 1 full gait cycle for 1 foot. Defined as heel-strike
    (initial contact) to heel-strike for the same foot. A basic asymmetry measure is also computed
    as the difference between sequential stride times of opposite feet
    """
    def __init__(self):
        super().__init__('stride time')

    @basic_asymmetry
    def _predict(self, dt, leg_length, gait, gait_aux):
        mask, mask_ofst = self._predict_init(gait, True, 2)
        gait[self.k_][mask] = (gait['IC'][mask_ofst] - gait['IC'][mask]) * dt


class StanceTime(EventMetric):
    """
    Stance time is the time during which the foot is on the ground. Defined as heel-strike
    (initial contact) to toe-off (final contact) for a foot. A basic asymmetry measure is also
    computed as the difference between sequential stance times of opposite feet
    """
    def __init__(self):
        super().__init__('stance time')

    @basic_asymmetry
    def _predict(self, dt, leg_length, gait, gait_aux):
        gait[self.k_] = (gait['FC'] - gait['IC']) * dt


class SwingTime(EventMetric):
    """
    Swing time is the time during which the foot is off the ground. Defined as toe-off
    (final contact) to heel-strike (initial contact) of the same foot. A basic asymmetry measure
    is also computed as the difference between sequential swing times of opposite feet
    """
    def __init__(self):
        super().__init__('swing time')

    @basic_asymmetry
    def _predict(self, dt, leg_length, gait, gait_aux):
        mask, mask_ofst = self._predict_init(gait, True, 2)
        gait[self.k_][mask] = (gait['IC'][mask_ofst] - gait['FC'][mask]) * dt


class StepTime(EventMetric):
    """
    Step time is the duration from heel-strike (initial contact) to heel-strike of the opposite
    foot. A basic asymmetry measure is also computed as the difference between sequential
    step times of opposite feet
    """
    def __init__(self):
        super().__init__('step time')

    @basic_asymmetry
    def _predict(self, dt, leg_length, gait, gait_aux):
        mask, mask_ofst = self._predict_init(gait, True, 1)
        gait[self.k_][mask] = (gait['IC'][mask_ofst] - gait['IC'][mask]) * dt


class InitialDoubleSupport(EventMetric):
    """
    Initial double support is the time immediately following heel strike during which the
    opposite foot is still on the ground. Defined as heel-strike (initial contact) to toe-off
    (final contact) of the opposite foot. A basic asymmetry measure is also computed as the
    difference between sequential initial double support times of opposite feet
    """
    def __init__(self):
        super().__init__('initial double support')

    @basic_asymmetry
    def _predict(self, dt, leg_length, gait, gait_aux):
        gait[self.k_] = (gait['FC opp foot'] - gait['IC']) * dt


class TerminalDoubleSupport(EventMetric):
    """
    Terminal double support is the time immediately before toe-off (final contact) in which
    the opposite foot has contacted the ground. Defined as heel-strike (initial contact) of the
    opposite foot to toe-off of the current foot. A basic asymmetry measure is also computed as
    the difference between sequential terminal double support times of opposite feet
    """
    def __init__(self):
        super().__init__('terminal double support')

    @basic_asymmetry
    def _predict(self, dt, leg_length, gait, gait_aux):
        mask, mask_ofst = self._predict_init(gait, True, 1)
        gait[self.k_][mask] = (gait['FC opp foot'][mask_ofst] - gait['IC'][mask_ofst]) * dt


class DoubleSupport(EventMetric):
    """
    Double support is the combined initial and terminal double support times. It is the total
    time during a stride that the current and opposite foot are in contact with the ground. A
    basic asymmetry measure is also computed as the difference between sequential double support
    times of opposite feet
    """
    def __init__(self):
        super().__init__('double support', depends=[InitialDoubleSupport, TerminalDoubleSupport])

    @basic_asymmetry
    def _predict(self, dt, leg_length, gait, gait_aux):
        gait[self.k_] = gait['PARAM:initial double support'] \
                        + gait['PARAM:terminal double support']


class SingleSupport(EventMetric):
    """
    Single support is the time during a stride that only the current foot is in contact with
    the ground. Defined as opposite foot toe-off (final contact) to opposite foot heel-strike
    (initial contact). A basic asymmetry measure is also computed as the difference between
    sequential single support times of opposite feet
    """
    def __init__(self):
        super().__init__('single support')

    @basic_asymmetry
    def _predict(self, dt, leg_length, gait, gait_aux):
        mask, mask_ofst = self._predict_init(gait, True, 1)
        gait[self.k_][mask] = (gait['IC'][mask_ofst] - gait['FC opp foot'][mask]) * dt


class StepLength(EventMetric):
    """
    Step length is the distance traveled during a step (heel-strike to opposite foot
    heel-strike). A basic asymmetry measure is also computed as the difference between sequential
    step lengths of opposite feet

    Notes
    -----
    The step length is computed using the inverted pendulum model from [1]_:

    .. math:: L_{step} = 2\\sqrt{2l_{leg}h-h^2}

    where :math:`L_{step}` is the step length, :math:`l_{leg}` is the leg length, and
    :math:`h` is the Center of Mass change in height during a step. Leg length can either be
    measured, or taken to be :math:`0.53height`

    References
    ----------
    .. [1] W. Zijlstra and A. L. Hof, “Assessment of spatio-temporal gait parameters from
        trunk accelerations during human walking,” Gait & Posture, vol. 18, no. 2, pp. 1–10,
        Oct. 2003, doi: 10.1016/S0966-6362(02)00190-X.
    """
    def __init__(self):
        super().__init__('step length')

    @basic_asymmetry
    def _predict(self, dt, leg_length, gait, gait_aux):
        if leg_length is not None:
            gait[self.k_] = 2 * sqrt(2 * leg_length * gait['delta h'] - gait['delta h']**2)
        else:
            self._predict_init(gait, True, None)  # don't generate masks


class StrideLength(EventMetric):
    r"""
    Stride length is the distance traveled during a stride (heel-strike to current foot
    heel-strike). A basic asymmetry measure is also computed as the difference between sequential
    stride lengths of opposite feet

    Notes
    -----
    The stride length is computed using the inverted pendulum model from [1]_:

    .. math:: L_{step} = 2\sqrt{2l_{leg}h-h^2}
    .. math:: L_{stride} = L_{step, i} + L_{step, i+1}

    where :math:`L_{s}` is the step or stride length, :math:`l_{leg}` is the leg length, and
    :math:`h` is the Center of Mass change in height during a step. Leg length can either be
    measured, or taken to be :math:`0.53height`

    References
    ----------
    .. [1] W. Zijlstra and A. L. Hof, “Assessment of spatio-temporal gait parameters from
        trunk accelerations during human walking,” Gait & Posture, vol. 18, no. 2, pp. 1–10,
        Oct. 2003, doi: 10.1016/S0966-6362(02)00190-X.
    """
    def __init__(self):
        super().__init__('stride length', depends=[StepLength])

    @basic_asymmetry
    def _predict(self, dt, leg_length, gait, gait_aux):
        mask, mask_ofst = self._predict_init(gait, True, 1)
        if leg_length is not None:
            gait[self.k_][mask] = gait['PARAM:step length'][mask_ofst] \
                            + gait['PARAM:step length'][mask]


class GaitSpeed(EventMetric):
    """
    Gait speed is how fast distance is being convered. Defined as the stride length divided by the
    stride duration, in m/s. A basic asymmetry measure is also computed as the difference between
    sequential gait speeds of opposite feet
    """
    def __init__(self):
        super().__init__('gait speed', depends=[StrideLength, StrideTime])

    @basic_asymmetry
    def _predict(self, dt, leg_length, gait, gait_aux):
        if leg_length is not None:
            gait[self.k_] = gait['PARAM:stride length'] / gait['PARAM:stride time']
        else:
            self._predict_init(gait, True, None)  # don't generate masks


class Cadence(EventMetric):
    """
    Cadence is the number of steps taken in 1 minute. It is computed per step, as 60.0s
    divided by the step time
    """
    def __init__(self):
        super().__init__('cadence', depends=[StepTime])

    def _predict(self, dt, leg_length, gait, gait_aux):
        gait[self.k_] = 60.0 / gait['PARAM:step time']


class IntraStrideCovariance(EventMetric):
    """
    Intra-stride covariance is the autocovariance of 1 stride with lag equal to the stride
    duration. In other words, it is how similar the acceleration signal is from one stride to the
    next for only 1 stride. Values near 1 indicate very symmetrical strides. It differs from the
    `StrideRegularity` in that stride regularity uses the acceleration from the entire gait bout
    while intra-stride covariance uses the acceleration only from individual strides. Values close
    to 1 indicate that the following stride was very similar/symmetrical to the current stride,
    while values close to 0 indicate that the following stride was not similar

    References
    ----------
    .. [1] C. Buckley et al., “Gait Asymmetry Post-Stroke: Determining Valid and Reliable
        Methods Using a Single Accelerometer Located on the Trunk,” Sensors, vol. 20, no. 1,
        Art. no. 1, Jan. 2020, doi: 10.3390/s20010037.
    """
    def __init__(self):
        super().__init__('intra-stride covariance - V')

    def _predict(self, dt, leg_length, gait, gait_aux):
        mask, mask_ofst = self._predict_init(gait, True, 2)

        i1 = gait['IC'][mask]
        i2 = gait['IC'][mask_ofst]
        i3 = i2 + (i2 - i1)

        for i, idx in enumerate(nonzero(mask)[0]):
            gait[self.k_][idx] = _autocovariance(
                # index the accel, then the list of views, then the vertical axis
                gait_aux['accel'][gait_aux['inertial data i'][idx]][:, gait_aux['vert axis']],
                i1[i], i2[i], i3[i], biased=False
            )


class IntraStepCovariance(EventMetric):
    """
    Intra-step covariance is the autocovariance of 1 step with lag equal to the step
    duration. In other words, it is how similar the acceleration signal is from one step to the
    next for only 1 step. Values near 1 indicate very symmetrical steps. It differs from the
    `StepRegularity` in that step regularity uses the acceleration from the entire gait bout
    while intra-step covariance uses the acceleration only from individual steps. Values close to
    1 indicate that the following step was very similar/symmetrical to the current step, while
    values close to 0 indicate that the following step was not similar

    References
    ----------
    .. [1] C. Buckley et al., “Gait Asymmetry Post-Stroke: Determining Valid and Reliable
        Methods Using a Single Accelerometer Located on the Trunk,” Sensors, vol. 20, no. 1,
        Art. no. 1, Jan. 2020, doi: 10.3390/s20010037.
    """
    def __init__(self):
        super().__init__('step regularity - V')

    def _predict(self, dt, leg_length, gait, gait_aux):
        mask, mask_ofst = self._predict_init(gait, True, 1)

        i1 = gait['IC'][mask]
        i2 = gait['IC'][mask_ofst]
        i3 = i2 + (i2 - i1)

        for i, idx in enumerate(nonzero(mask)[0]):
            gait[self.k_][idx] = _autocovariance(
                gait_aux['accel'][gait_aux['inertial data i'][idx]][:, gait_aux['vert axis']],
                i1[i], i2[i], i3[i], biased=False
            )


class HarmonicRatioV(EventMetric):
    r"""
    Harmonic Ratio (HR) assesses the symmetry of the 2 steps that occur during each stride. Defined
    as the sum of the amplitude of the even harmonics (of average stride frequency) divided by the
    sum of the amplitude of the odd harmonics. Higher values indicate better symmetry between the
    steps occuring during an individual stride

    Notes
    -----
    The Harmonic ratio is computed from the first 20 harmonics extracted from a fourier series.
    For the vertical direction, the HR is defined as

    .. math:: HR = \frac{\sum_{n=1}^{10}F(2nf_{stride})}{\sum_{n=1}^{10}F((2n-1)f_{stride})}

    where :math:`F` is the power spectral density and :math:`f_{stride}` is the stride frequency.
    Since this is computed on a per-stride basis, the stride frequency is estimated as the inverse
    of stride time for the individual stride.

    References
    ----------
    .. [1] J. L. Roche, K. A. Lowry, J. M. Vanswearingen, J. S. Brach, and M. S. Redfern,
        “Harmonic Ratios: A quantification of step to step symmetry,” J Biomech, vol. 46, no. 4,
        pp. 828–831, Feb. 2013, doi: 10.1016/j.jbiomech.2012.12.008.
    .. [2] C. Buckley et al., “Gait Asymmetry Post-Stroke: Determining Valid and Reliable
        Methods Using a Single Accelerometer Located on the Trunk,” Sensors, vol. 20, no. 1,
        Art. no. 1, Jan. 2020, doi: 10.3390/s20010037.
    """

    def __init__(self):
        super().__init__('harmonic ratio - V', depends=[StrideTime])
        self._freq = fft.rfftfreq(1024)  # precompute the frequencies (still need to be scaled)
        # TODO add check for stride frequency, if too low, bump this up higher?
        self._harmonics = arange(1, 21, dtype=int_)

    def _predict(self, dt, leg_length, gait, gait_aux):
        mask, mask_ofst = self._predict_init(gait, init=True, offset=2)

        i1 = gait['IC'][mask]
        i2 = gait['IC'][mask_ofst]

        va = gait_aux['vert axis']  # shorthand

        for i, idx in enumerate(nonzero(mask)[0]):
            F = abs(fft.rfft(
                gait_aux['accel'][gait_aux['inertial data i'][idx]][i1[i]:i2[i], va],
                n=1024
            ))
            stridef = 1 / gait['PARAM:stride time'][idx]  # current stride frequency
            # get the indices for the first 20 harmonics
            ix_stridef = argmin(abs(self._freq / dt - stridef)) * self._harmonics

            # index 1 is harmonic 2 -> even harmonics / odd harmonics
            gait[self.k_][idx] = sum(F[ix_stridef[1::2]]) / sum(F[ix_stridef[::2]])


# ===========================================================
#     GAIT BOUT-LEVEL METRICS
# ===========================================================
class PhaseCoordinationIndex(BoutMetric):
    r"""
    Phase Coordination Index (PCI) assesses symmetry between steps during straight overground gait.
    Computed for an entire bout, it is a measure of the deviation from symmetrical steps (ie half a
    stride is equal to exactly 1 step duration). Lower values indicate better symmetry and
    a "more consistent and accurate phase generation" [2]_

    Notes
    -----
    The computation of PCI relies on the assumption that healthy gait is perfectly even, with
    step times being exactly half of stride times. This assumption informs the definition
    of the PCI, where the perfect step phase is set to :math:`180^\circ`. To compute PCI, the
    phase is first computed for each stride as the relative step to stride time in degrees,

    .. math:: \varphi_i = 360^\circ\left(\frac{hs_{i+1}-hs_{i}}{hs_{i+2}-hs{i}}\right)

    where :math:`hs_i` is the *ith* heel-strike. Then over the whole bout, the mean absolute
    difference from :math:`180^\circ` is computed as :math:`\varphi_{ABS}`,

    .. math:: \varphi_{ABS} = \frac{1}{N}\sum_{i=1}^{N}|\varphi_i - 180^\circ|

    The coefficient of variation (:math:`\varphi_{CV}`) is also computed for phase,

    .. math: \varphi_{CV} = 100\frac{s_{\varphi}}{\bar{\varphi}}

    where :math:`\bar{\varphi}` and :math:`s_{\varphi}` are the sample mean and standard deviation
    of :math:`\varphi` respectively. Finally, the PCI is computed,

    .. math:: PCI = \varphi_{CV} + 100\frac{\varphi_{ABS}}{180}

    References
    ----------
    .. [1] M. Plotnik, N. Giladi, and J. M. Hausdorff, “A new measure for quantifying the
        bilateral coordination of human gait: effects of aging and Parkinson’s disease,”
        Exp Brain Res, vol. 181, no. 4, pp. 561–570, Aug. 2007, doi: 10.1007/s00221-007-0955-7.
    .. [2] A. Weiss, T. Herman, N. Giladi, and J. M. Hausdorff, “Association between Community
        Ambulation Walking Patterns and Cognitive Function in Patients with Parkinson’s Disease:
        Further Insights into Motor-Cognitive Links,” Parkinsons Dis, vol. 2015, 2015,
        doi: 10.1155/2015/547065.
    """
    def __init__(self):
        super().__init__('phase coordination index')

    def _predict(self, dt, leg_length, gait, gait_aux):
        pci = zeros(len(gait_aux['accel']), dtype=float_)

        phase = gait['PARAM:step time'] / gait['PARAM:stride time']  # %, not degrees
        for i in range(len(gait_aux['accel'])):
            mask = gait_aux['inertial data i'] == i

            psi_abs = nanmean(abs(phase[mask] - 0.5))  # using % not degrees right now
            psi_cv = nanstd(phase[mask], ddof=1, ) / nanmean(phase[mask])

            pci[i] = 100 * (psi_cv + psi_abs / 0.5)

        gait[self.k_] = pci[gait_aux['inertial data i']]


class GaitSymmetryIndex(BoutMetric):
    r"""
    Gait Symmetry Index (GSI) assesses symmetry between steps during straight overground gait. It
    is computed for a whole bout. Values closer to 1 indicate higher symmetry, while values close
    to 0 indicate lower symmetry

    Notes
    -----
    GSI is computed using the biased autocovariance of the acceleration after being filtered
    through a 4th order 10Hz cutoff butterworth low-pass filter. [1]_ and [2]_ use the
    autocorrelation, instead of autocovariance, however subtracting from the compared signals
    results in a better mathematical comparison of the symmetry of the acceleration profile of the
    gait. The biased autocovariance is used to suppress the value at higher lags [1]_. In order to
    ensure that full steps/strides are capture, the maximum lag for the autocorrelation is set to
    4s, which should include several strides in healthy adults, and account for more than
    2.5 strides in impaired populations, such as hemiplegic stroke patients [3]_.

    With the autocovariances computed for all 3 acceleration axes, the coefficient of stride
    repetition (:math:`C_{stride}`) is computed for lag :math:`m` per

    .. math:: C_{stride}(m) = K_{AP}(m) + K_{V}(m) + K_{ML}(m)

    where :math:`K_{x}` is the autocovariance in the :math:`x` direction - Anterior-Posterior (AP),
    Medial-Lateral (ML), or vertical (V). The coefficient of step repetition (:math:`C_{step}`)
    is the norm of :math:`C_{stride}`

    .. math:: C_{step}(m) = \sqrt{C_{stride}(m)} = \sqrt{K_{AP}(m) + K_{V}(m) + K_{ML}(m)}

    Under the assumption that perfectly symmetrical gait will have step durations equal to half
    the stride duration, the GSI is computed per

    .. math:: GSI = C_{step}(0.5m_{stride}) / \sqrt{3}

    where :math:`m_{stride}` is the lag for the average stride in the gait bout, and corresponds to
    a local maximum in the autocovariance function. To find the peak corresponding to
    :math:`m_{stride}` the peak nearest to the average stride time for the bout is used. GSI is
    normalized by :math:`\sqrt{3}` in order to have a maximum value of 1.

    References
    ----------
    .. [1] W. Zhang, M. Smuck, C. Legault, M. A. Ith, A. Muaremi, and K. Aminian, “Gait Symmetry
        Assessment with a Low Back 3D Accelerometer in Post-Stroke Patients,” Sensors, vol. 18,
        no. 10, p. 3322, Oct. 2018, doi: 10.3390/s18103322.
    .. [2] C. Buckley et al., “Gait Asymmetry Post-Stroke: Determining Valid and Reliable
        Methods Using a Single Accelerometer Located on the Trunk,” Sensors, vol. 20, no. 1,
        Art. no. 1, Jan. 2020, doi: 10.3390/s20010037.
    .. [3] H. P. von Schroeder, R. D. Coutts, P. D. Lyden, E. Billings, and V. L. Nickel, “Gait
        parameters following stroke: a practical assessment,” Journal of Rehabilitation Research
        and Development, vol. 32, no. 1, pp. 25–31, Feb. 1995.
    """
    def __init__(self):
        super().__init__('gait symmetry index')

    def _predict(self, dt, leg_length, gait, gait_aux):
        gsi = zeros(len(gait_aux['accel']), dtype=float_)

        # setup acceleration filter
        sos = butter(4, 2 * 10 * dt, btype='low', output='sos')
        for i, acc in enumerate(gait_aux['accel']):
            lag = int(
                round(nanmean(gait['PARAM:stride time'][gait_aux['inertial data i'] == i]) / dt)
            )
            # GSI uses biased autocovariance
            ac = _autocovariancefunction(sosfiltfilt(sos, acc, axis=0), int(4.5 * dt), biased=True)

            # C_stride is the sum of 3 axes
            pks, _ = find_peaks(sum(ac, axis=1))
            # find the closest peak to the computed ideal half stride lag
            idx = int(0.5 * argmin(abs(pks - lag)))

            gsi[i] = sqrt(sum(ac[idx])) / sqrt(3)

        gait[self.k_] = gsi[gait_aux['inertial data i']]


class StepRegularityV(BoutMetric):
    """
    Step regularity is the autocorrelation at a lag time of 1 step. Computed for an entire bout
    of gait, this is a measure of the average symmetry of sequential steps during overground
    strait gait for the vertical acceleration component. Values close to 1 indicate high degree of
    regularity/symmetry, while values close to 0 indicate a low degree of regularity/symmetry

    Notes
    -----
    Step regularity is the value of the autocovariance function at a lag equal to the time
    for one step. While [2]_ uses the autocorrelation instead of the autocovariance like [1]_, the
    autocovariance is used here as it provides a mathematically better comparison of the
    acceleration profile during gait.

    The peak corresponding to one step time is found by searching the area near the lag
    corresponding to the average step time for the gait bout. The nearest peak to this point is
    used as the peak at a lag of one step.

    References
    ----------
    .. [1] R. Moe-Nilssen and J. L. Helbostad, “Estimation of gait cycle characteristics by trunk
        accelerometry,” Journal of Biomechanics, vol. 37, no. 1, pp. 121–126, Jan. 2004,
        doi: 10.1016/S0021-9290(03)00233-1.
    .. [2] C. Buckley et al., “Gait Asymmetry Post-Stroke: Determining Valid and Reliable
        Methods Using a Single Accelerometer Located on the Trunk,” Sensors, vol. 20, no. 1,
        Art. no. 1, Jan. 2020, doi: 10.3390/s20010037.
    """
    def __init__(self):
        super().__init__('step regularity - V', depends=[StepTime])

    def _predict(self, dt, leg_length, gait, gait_aux):
        stepreg = zeros(len(gait_aux['accel']), dtype=float_)

        for i, acc in enumerate(gait_aux['accel']):
            lag = int(
                round(nanmean(gait['PARAM:step time'][gait_aux['inertial data i'] == i]) / dt)
            )
            acf = _autocovariancefunction(acc[:, gait_aux['vert axis']], int(4.5 * dt))
            pks, _ = find_peaks(acf)
            idx = argmin(abs(pks - lag))

            stepreg[i] = acf[idx]

        # broadcast step regularity into gait for each step
        gait[self.k_] = stepreg[gait_aux['inertial data i']]


class StrideRegularityV(BoutMetric):
    """
    Stride regularity is the autocorrelation at a lag time of 1 stride. Computed for an entire bout
    of gait, this is a measure of the average symmetry of sequential stride during overground
    strait gait for the vertical acceleration component. Values close to 1 indicate high degree of
    regularity/symmetry, while values close to 0 indicate a low degree of regularity/symmetry

    Notes
    -----
    Stride regularity is the value of the autocovariance function at a lag equal to the time
    for one stride. While [2]_ uses the autocorrelation instead of the autocovariance like [1]_,
    the autocovariance is used here as it provides a mathematically better comparison of the
    acceleration profile during gait.

    The peak corresponding to one stride time is found by searching the area near the lag
    corresponding to the average stride time for the gait bout. The nearest peak to this point is
    used as the peak at a lag of one stride.

    References
    ----------
    .. [1] R. Moe-Nilssen and J. L. Helbostad, “Estimation of gait cycle characteristics by trunk
        accelerometry,” Journal of Biomechanics, vol. 37, no. 1, pp. 121–126, Jan. 2004,
        doi: 10.1016/S0021-9290(03)00233-1.
    .. [2] C. Buckley et al., “Gait Asymmetry Post-Stroke: Determining Valid and Reliable
        Methods Using a Single Accelerometer Located on the Trunk,” Sensors, vol. 20, no. 1,
        Art. no. 1, Jan. 2020, doi: 10.3390/s20010037.
    """
    def __init__(self):
        super().__init__('stride regularity - V', depends=[StrideTime])

    def _predict(self, dt, leg_length, gait, gait_aux):
        stridereg = zeros(len(gait_aux['accel']), dtype=float_)

        for i, acc in enumerate(gait_aux['accel']):
            # acf = _autocovariancefunction(acc[:, gait_aux['vert axis']], int(4.5 / dt))
            # compute the average number of samples per stride, this *should* be the
            # lag over the bout for sequential strides
            lag = int(
                round(nanmean(gait['PARAM:stride time'][gait_aux['inertial data i'] == i]) / dt)
            )
            acf = _autocovariancefunction(acc[:, gait_aux['vert axis']], int(4.5 * dt))
            pks, _ = find_peaks(acf)
            idx = argmin(abs(pks - lag))

            stridereg[i] = acf[idx]

        # broadcast step regularity into gait for each step
        gait[self.k_] = stridereg[gait_aux['inertial data i']]


class AutocorrelationSymmetryV(BoutMetric):
    """
    Autocorrelation symmetry is the absolute difference between stride and step regularity. It
    quantifies the level of symmetry between the stride and step regularity and provide an overall
    metric of symmetry for the gait bout

    References
    ----------
    .. [1] C. Buckley et al., “Gait Asymmetry Post-Stroke: Determining Valid and Reliable
        Methods Using a Single Accelerometer Located on the Trunk,” Sensors, vol. 20, no. 1,
        Art. no. 1, Jan. 2020, doi: 10.3390/s20010037.
    """
    def __init__(self):
        super().__init__(
            'autocorrelation symmetry - V', depends=[StepRegularityV, StrideRegularityV]
        )

    def _predict(self, dt, leg_length, gait, gait_aux):
        gait[self.k_] = abs(
            gait['BOUTPARAM:step regularity - V'] - gait['BOUTPARAM:stride regularity - V']
        )
