import math

import numpy as np
from scipy.integrate import solve_ivp
from tud_rl.envs._envs.FossenFnc import (angle_to_2pi, angle_to_pi, dtr,
                                         polar_from_xy, rtd)


class KVLCC2:
    """This class provides a KVLCC2 tanker behaving according to the MMG standard model of Yasukawa, Yoshimura (2015)."""

    def __init__(self, N_init, E_init, psi_init, u_init, v_init, r_init, delta_t, N_max, E_max) -> None:

        #------------------------- Parameter/Settings -----------------------------------

        # store simulation settings and dummy action for rendering
        self.delta_t = delta_t
        self.N_max   = N_max
        self.E_max   = E_max
        self.action  = 0

        # KVLCC2 parameters
        self.kvlcc2_full = {
            "C_b":          0.810,          # Block Coefficient
            "Lpp":          320.0,          # Length over pependiculars (m)
            "B":            58.,            # Overall width
            "m":            312_600*1000,   # Mass of ship as calculated by ▽*rho (displacement * water density)
            "w_P0":         0.35,           # Assumed wake fraction coefficient
            "J_int":        0.4,            # Intercept for the calculation of K_T (https://doi.org/10.1615/ICHMT.2012.ProcSevIntSympTurbHeatTransfPal.500)
            "J_slo":       -0.5,            # Slope for the calculation of K_T
            "x_G":          11.2,           # X-Coordinate of the center of gravity (m)
            "x_P":         -160.0,          # X-Coordinate of the propeller (-0.5*Lpp)
            "D_p":          9.86,           # Diameter of propeller (m)
            "k_0":          0.2931,         # Same value as "J_int" | Propeller open water coefficients. 
            "k_1":         -0.2753,
            "k_2":         -0.1359,    
            "C_1":          2.0,
            "C_2_plus":     1.6,
            "C_2_minus":    1.1,
            "l_R":         -0.710,          # correction of flow straightening factor to yaw-rate
            "gamma_R":      None,      
            "gamma_R_plus": 0.640,          # Flow straightening coefficient for positive rudder angles
            "gamma_R_minus":0.395,          # Flow straightening coefficient for negative rudder angles
            "eta_param":    0.626,          # Ratio of propeller diameter to rudder span
            "kappa":        0.50,           # An experimental constant for expressing "u_R"
            "A_R":          112.5,          # Moveable rudder area
            "epsilon":      1.09,           # Ratio of wake fraction at propeller and rudder positions ((1 - w_R) / (1 - w_P))
            "A_R_Ld_em":    1/46.8,         # Fraction of moveable Rudder area to length*draft
            "f_alpha":      2.747,          # Rudder lift gradient coefficient (assumed rudder aspect ratio = 2)
            "rho":          1000,           # Water density of freshwater
            "t_R":          0.387,          # Steering resistance deduction factor
            "t_P":          0.220,          # Thrust deduction factor. TODO give this more than an arbitrary value
            "x_H_dash":    -0.464,          # Longitudinal coordinate of acting point of the additional lateral force
            "d":            20.8,           # Ship draft (Tiefgang)
            "m_x_dash":     0.022,          # Non dimensionalized added masses coefficient in x direction
            "m_y_dash":     0.223,          # Non dimensionalized added masses coefficient in y direction
            "R_0_dash":     0.022,          # frictional resistance coefficient TODO Estimate this via Schoenherr's formula
            "X_vv_dash":   -0.040,          # Hull derivatives
            "X_vr_dash":    0.002,          # Hull derivatives
            "X_rr_dash":    0.011,          # Hull derivatives
            "X_vvvv_dash":  0.771,          # Hull derivatives
            "Y_v_dash":    -0.315,          # Hull derivatives
            "Y_r_dash":     0.083,          # Hull derivatives
            "Y_vvv_dash":  -1.607,          # Hull derivatives
            "Y_vvr_dash":   0.379,          # Hull derivatives
            "Y_vrr_dash":  -0.391,          # Hull derivatives
            "Y_rrr_dash":   0.008,          # Hull derivatives
            "N_v_dash":    -0.137,          # Hull derivatives
            "N_r_dash":    -0.049,          # Hull derivatives
            "N_vvv_dash":  -0.030,          # Hull derivatives
            "N_vvr_dash":  -0.294,          # Hull derivatives
            "N_vrr_dash":   0.055,          # Hull derivatives
            "N_rrr_dash":  -0.013,          # Hull derivatives
            "I_zG":         2e12,           # Moment of inertia of ship around center of gravity (m*(0.25*Lpp)**2) (Point mass Inertia)
            "J_z_dash":     0.011,          # Added moment of inertia coefficient
            "a_H":          0.312           # Rudder force increase factor
        }
        for key, value in self.kvlcc2_full.items():
            setattr(self, key, value)

        #------------------------- Motion Initialization -----------------------------------
        # Propeller revolutions [s⁻¹]
        self.nps = 4.0

        # rudder angle max (in rad) and increment (in rad/s)
        self.rud_angle_max = dtr(10)
        self.rud_angle_inc = dtr(2.5) * self.delta_t

        # init rudder angle
        self.rud_angle = 0

        # eta, nu
        self.eta = np.array([N_init, E_init, psi_init], dtype=np.float32)  # N (in m),   E (in m),   psi (in rad)   in NE-system
        self.nu  = np.array([u_init, v_init, r_init], dtype=np.float32)    # u (in m/s), v in (m/s), r (in rad/s)   in BODY-system

        self.nu_dot  = np.array([0.0, 0.0, 0.0], dtype=np.float32)

    def _T_of_psi(self, psi):
        """Computes rotation matrix for given heading (in rad)."""
        return np.array([[np.cos(psi), -np.sin(psi), 0.],
                         [np.sin(psi),  np.cos(psi), 0.],
                         [0., 0., 1.]])

    def _C_X(g_rc: float) -> float:
        return (-0.0665*g_rc**5 + 
                0.5228*g_rc**4 - 
                1.4365*g_rc**3 + 
                1.6024*g_rc**2 - 
                0.2967*g_rc - 
                0.4691)

    def _C_Y(g_rc: float) -> float:
        return (0.05930686*g_rc**4 -
                0.37522028*g_rc**3 +
                0.46812233*g_rc**2 +
                0.39114522*g_rc -
                0.00273578)

    def _C_N(g_rc: float) -> float:
        return (-0.0140*g_rc**5 + 
                0.1131*g_rc**4 -
                0.2757*g_rc**3 + 
                0.1617*g_rc**2 + 
                0.0728*g_rc)

    def _vm_from_v_r(self, v, r):
        """v = lateral velocity midship + x coord center of gravity * yaw rate"""
        return v - self.x_G * r

    def _mmg_dynamics(self, t, y, fl_psi, fl_vel) -> np.ndarray:
        """System of ODEs after Yasukawa, H., Yoshimura, Y. (2015) for the MMG standard model.
        Args:
            t (np.ndarray):  time
            y (np.ndarray):  [N, E, psi, u, v, r] which is [eta, nu]
            fl_psi (float):  attack angle of current relative longitudinal axis of motion [rad]
            fl_vel (float):  velocity of current [m/s]
        Returns:
            np.ndarray: [eta_dot, nu_dot]
        """

        # unpack values
        _, _, psi, u, v, r = y

        vm = self._vm_from_v_r(v, r)
        U = math.sqrt(u**2 + vm**2)  # overall speed of the vessel

        if U == 0.0:  # no vessel movement. Velocity in all directions = 0
            beta = 0.0
            v_dash = 0.0
            r_dash = 0.0
        else:
            beta = math.atan2(-vm, u)   # drift angle at midship position
            v_dash = vm / U             # non-dimensionalized lateral velocity
            r_dash = r * self.Lpp / U   # non-dimensionalized yaw rate

        # redefine
        beta_P = beta - (self.x_P/self.Lpp) * r_dash
        if all([key in self.kvlcc2_full.keys() for key in ["C_1","C_2_plus","C_2_minus"]]):
            C_2 = self.C_2_plus if beta_P >= 0 else self.C_2_minus
            w_P = -(1+(1-math.exp(-self.C_1*abs(beta_P))*(C_2-1))*(1-self.w_P0))+1
        else:
            w_P = self.w_P0 * math.exp(-4.0 * (beta_P)**2)

        if self.nps == 0.0:  # no propeller movement, no advance ratio
            J = 0.0
        else:
            J = (1 - w_P) * u / (self.nps * self.D_p)  # propeller advance ratio

        if all([key in self.kvlcc2_full.keys() for key in ["k_0", "k_1", "k_2"]]):
            # propeller thrust open water characteristic
            K_T = self.k_0 + (self.k_1 * J) + (self.k_2 * J**2)
        else:
            # inferred slope + intercept dependent on J (empirical)
            K_T = self.J_slo * J + self.J_int

        # effective inflow angle to rudder in maneuvering motions
        beta_R = beta - self.l_R * r_dash

        # flow straightening coefficient
        if self.gamma_R is not None:
            gamma_R = self.gamma_R
        else:
            if beta_R < 0.0:
                gamma_R = self.gamma_R_minus
            else:
                gamma_R = self.gamma_R_plus

        # lateral inflow velocity components to rudder
        v_R = U * gamma_R * beta_R

        # longitudinal inflow velocity components to rudder
        if J == 0.0:
            u_R = math.sqrt(self.eta_param * (self.kappa * self.epsilon *
                                        8.0 * self.k_0 * self.nps ** 2 * self.D_p**4 / np.pi)**2)
        else:
            u_R = u * (1 - w_P) * self.epsilon * math.sqrt(
                self.eta_param * (1.0 + self.kappa * (
                    math.sqrt(1.0 + 8.0 * K_T / (np.pi * J**2)) - 1))**2 + (1 - self.eta_param)
            )
        # rudder inflow velocity
        U_R = math.sqrt(u_R**2 + v_R**2)

        # rudder inflow angle
        alpha_R = self.rud_angle - math.atan2(v_R, u_R)

        # normal force on rudder
        if self.A_R is not None:
            F_N = 0.5 * self.A_R * self.rho * \
                self.f_alpha * (U_R**2) * math.sin(alpha_R)
        else:
            F_N = 0.5 * self.A_R_Ld_em * (self.Lpp * self.d * self.rho) * \
                self.f_alpha * (U_R**2) * math.sin(alpha_R)

        # longitudinal surge force around midship acting on ship hull
        X_H = (0.5 * self.rho * self.Lpp * self.d * (U**2) * (
            - self.R_0_dash
            + self.X_vv_dash * (v_dash**2)
            + self.X_vr_dash * v_dash * r_dash
            + self.X_rr_dash * (r_dash**2)
            + self.X_vvvv_dash * (v_dash**4)
        )
        )

        # longitudinal surge force around midship by steering
        X_R = -(1 - self.t_R) * F_N * math.sin(self.rud_angle)

        # longitudinal Surge force due to propeller
        X_P = (1 - self.t_P) * self.rho * K_T * self.nps**2 * self.D_p**4

        # longitudinal surge force around midship acting on ship hull
        Y_H = (0.5 * self.rho * self.Lpp * self.d * (U**2) * (
            self.Y_v_dash * v_dash
            + self.Y_r_dash * r_dash
            + self.Y_vvv_dash * (v_dash**3)
            + self.Y_vvr_dash * (v_dash**2) * r_dash
            + self.Y_vrr_dash * v_dash * (r_dash**2)
            + self.Y_rrr_dash * (r_dash**3)
        )
        )

        # lateral surge force by steering
        Y_R = -(1 + self.a_H) * F_N * math.cos(self.rud_angle)

        # yaw moment around midship acting on ship hull
        N_H = (0.5 * self.rho * (self.Lpp**2) * self.d * (U**2) * (
            self.N_v_dash * v_dash
            + self.N_r_dash * r_dash
            + self.N_vvv_dash * (v_dash**3)
            + self.N_vvr_dash * (v_dash**2) * r_dash
            + self.N_vrr_dash * v_dash * (r_dash**2)
            + self.N_rrr_dash * (r_dash**3)
        )
        )

        # redimensionalize x_H
        x_H = self.x_H_dash * self.Lpp

        # yaw moment around midship by steering
        N_R = -((-1/2) + self.a_H * x_H) * F_N * math.cos(self.rud_angle)

        # forces related to currents:
        if fl_vel is not None and fl_vel != 0.:
            
            # longitudinal velocity of current dependent on ship heading
            u_c = -fl_vel * math.cos(fl_psi - psi)
            u_rc = u - u_c

            # lateral velocity of current dependent on ship heading
            v_c = fl_vel * math.sin(fl_psi - psi)
            v_rc = vm - v_c

            g_rc = abs(-math.atan2(v_rc,u_rc))

            # longitudinal current force
            A_Fc = self.B * self.d * self.C_b
            X_C = 0.5 * self.rho * A_Fc * self._C_X(g_rc) * abs(u_rc) * u_rc

            # lateral current force
            A_Lc = self.Lpp * self.d * self.C_b
            Y_C = 0.5 * self.rho * A_Lc * self._C_Y(g_rc) * abs(v_rc) * v_rc

            # current Moment
            N_C = 0.5 * self.rho * A_Lc * self.Lpp * self._C_N(g_rc) * abs(v_rc) * v_rc

        else:
            X_C, Y_C, N_C = 0.0, 0.0, 0.0

        # added masses and added moment of inertia
        m_x = self.m_x_dash * (0.5 * self.rho * (self.Lpp**2) * self.d)
        m_y = self.m_y_dash * (0.5 * self.rho * (self.Lpp**2) * self.d)
        J_z = self.J_z_dash * (0.5 * self.rho * (self.Lpp**4) * self.d)
        m = self.m
        I_zG = self.I_zG

        # longitudinal acceleration
        d_u = ((X_H + X_R + X_P + X_C) + (m + m_y) * v *
            r + self.x_G * m * (r**2)) / (m + m_x)

        # lateral acceleration
        f = (I_zG + J_z + (self.x_G**2) * m)

        d_v = ((Y_H+Y_R+Y_C) - (m+m_x)*u*r - ((self.x_G*m*(N_H + N_R + N_C))/(f)) + ((self.x_G**2*m**2*u*r)/(f)))\
            / ((m+m_y)-((self.x_G**2*m**2)/(f)))

        # yaw rate acceleration
        d_r = ((N_H + N_R + N_C) - (self.x_G * m * d_v + self.x_G * m * u * r)) / \
            (I_zG + J_z + (self.x_G**2) * m)

        # this constitutes nu_dot
        nu_dot = np.array([d_u, d_v, d_r])

        # get eta_dot
        eta_dot = np.dot(self._T_of_psi(psi), np.array([u, v, r]))

        return np.concatenate([eta_dot, nu_dot])


    def _upd_dynamics(self):
        """Updates positions and velocities for next simulation step."""

        # store current values for change rate computation
        self.nu_old = self.nu

        # integrate
        sol = solve_ivp(fun    = self._mmg_dynamics,
                        t_span = (0.0, self.delta_t),
                        y0     = np.concatenate([self.eta, self.nu]),
                        t_eval = np.array([self.delta_t]),
                        args   = (0.0, 0.0),   # fl_psi, fl_vel
                        method = "RK45")

        # store new eta and nu
        self.eta = sol.y[0:3, 0]
        self.nu  = sol.y[3:, 0]

        # transform heading to [0, 2pi)
        self.eta[2] = angle_to_2pi(self.eta[2])

        # get nu_dot           
        self.nu_dot  = (self.nu - self.nu_old) / self.delta_t


    def _control(self, a):
        """
        Action 'a' is an integer taking values in [0, 1, 2]. They correspond to:

        0 - keep rudder angle as is
        1 - increase rudder angle
        2 - decrease rudder angle
        """
        assert a in range(3), "Unknown action."

        # store action for rendering
        self.action = a
        
        # update angle
        if a == 0:
            pass
        elif a == 1:
            self.rud_angle += self.rud_angle_inc
        elif a == 2:
            self.rud_angle -= self.rud_angle_inc
        
        # clip it
        self.rud_angle = np.clip(self.rud_angle, -self.rud_angle_max, self.rud_angle_max)


    def _get_sideslip(self):
        """Returns the sideslip angle in radiant."""
        u, v, r = self.nu
        vm = self._vm_from_v_r(v, r)

        return math.atan2(-vm, u)
        #return polar_from_xy(x=vm, y=u, with_r=False, with_angle=True)[1]


    def _get_course(self):
        """Returns the course angle in radiant, which is heading + sideslip."""
        return angle_to_2pi(self.eta[2] + self._get_sideslip())


    def _get_V(self):
        """Returns the aggregated velocity."""
        u, v, r = self.nu
        vm = self._vm_from_v_r(v, r)
        return np.sqrt(u**2 + vm**2)


    def _is_off_map(self):
        """Checks whether vessel left the map."""

        if self.eta[0] <= 0 or self.eta[0] >= self.N_max or self.eta[1] <= 0 or self.eta[1] >= self.E_max:
            return True
        return False
