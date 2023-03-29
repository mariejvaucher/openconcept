import numpy as np
import openmdao.api as om


class WingMACTrapezoidal(om.ExplicitComponent):
    """
    Compute the mean aerodynamic chord of a trapezoidal planform.

    Inputs
    ------
    S_ref : float
        Wing planform area (scalar, sq m)
    AR : float
        Wing aspect ratio (scalar, dimensionless)
    taper : float
        Wing taper ratio (scalar, dimensionless)

    Outputs
    -------
    MAC : float
        Mean aerodynamic chord of the trapezoidal planform (scalar, m)
    """

    def setup(self):
        self.add_input("S_ref", units="m**2")
        self.add_input("AR")
        self.add_input("taper")
        self.add_output("MAC", lower=1e-6, units="m")
        self.declare_partials("MAC", "*")

    def compute(self, inputs, outputs):
        S = inputs["S_ref"]
        AR = inputs["AR"]
        taper = inputs["taper"]

        c_root = np.sqrt(S / AR) * 2 / (1 + taper)
        c_tip = taper * c_root
        outputs["MAC"] = 2 / 3 * (c_root + c_tip - c_root * c_tip / (c_root + c_tip))

    def compute_partials(self, inputs, J):
        S = inputs["S_ref"]
        AR = inputs["AR"]
        taper = inputs["taper"]

        c_root = np.sqrt(S / AR) * 2 / (1 + taper)
        dcr_dS = 0.5 / np.sqrt(S * AR) * 2 / (1 + taper)
        dcr_dAR = -0.5 * S**0.5 / AR**1.5 * 2 / (1 + taper)
        dcr_dtaper = -np.sqrt(S / AR) * 2 / (1 + taper) ** 2

        c_tip = taper * c_root

        dMAC_dcr = 2 / 3 * (1 - c_tip**2 / (c_root + c_tip) ** 2)
        dMAC_dct = 2 / 3 * (1 - c_root**2 / (c_root + c_tip) ** 2)

        J["MAC", "S_ref"] = (dMAC_dcr + dMAC_dct * taper) * dcr_dS
        J["MAC", "AR"] = (dMAC_dcr + dMAC_dct * taper) * dcr_dAR
        J["MAC", "taper"] = (dMAC_dcr + dMAC_dct * taper) * dcr_dtaper + dMAC_dct * c_root


class WingSpan(om.ExplicitComponent):
    """
    Compute the wing span as the square root of wing area times aspect ratio.

    Inputs
    ------
    S_ref : float
        Wing planform area (scalar, sq m)
    AR : float
        Wing aspect ratio (scalar, dimensionless)

    Outputs
    -------
    span : float
        Wing span (scalar, m)
    """

    def setup(self):
        self.add_input("S_ref", units="m**2")
        self.add_input("AR")

        self.add_output("span", units="m")
        self.declare_partials(["span"], ["*"])

    def compute(self, inputs, outputs):
        b = inputs["S_ref"] ** 0.5 * inputs["AR"] ** 0.5
        outputs["span"] = b

    def compute_partials(self, inputs, J):
        J["span", "S_ref"] = 0.5 * inputs["S_ref"] ** (0.5 - 1) * inputs["AR"] ** 0.5
        J["span", "AR"] = inputs["S_ref"] ** 0.5 * 0.5 * inputs["AR"] ** (0.5 - 1)


class WingAspectRatio(om.ExplicitComponent):
    """
    Compute the aspect ratio from span and wing area.

    Inputs
    ------
    S_ref : float
        Planform area (scalar, sq m)
    span : float
        Wing span (scalar, m)

    Outputs
    -------
    AR : float
        Aspect ratio, weighted by section areas (scalar, deg)
    """

    def setup(self):
        self.add_input("S_ref", units="m**2")
        self.add_input("span", units="m")
        self.add_output("AR", val=10.0, lower=1e-6)
        self.declare_partials("*", "*")

    def compute(self, inputs, outputs):
        outputs["AR"] = inputs["span"] ** 2 / inputs["S_ref"]

    def compute_partials(self, inputs, J):
        J["AR", "span"] = 2 * inputs["span"] / inputs["S_ref"]
        J["AR", "S_ref"] = -inputs["span"] ** 2 / inputs["S_ref"] ** 2


class WingSweepFromSections(om.ExplicitComponent):
    """
    Compute the average quarter chord sweep angle weighted by section areas
    by taking in sectional parameters as they would be defined for a
    sectional OpenAeroStruct mesh.

    Inputs
    ------
    x_LE_sec : float
        Streamwise offset of the section's leading edge, starting with the outboard
        section (wing tip) and moving inboard toward the root (vector of length
        num_sections, m)
    y_sec : float
        Spanwise location of each section, starting with the outboard section (wing
        tip) at the MOST NEGATIVE y value and moving inboard (increasing y value)
        toward the root; the user does not provide a value for the root because it
        is always 0.0 (vector of length num_sections - 1, m)
    chord_sec : float
        Chord of each section, starting with the outboard section (wing tip) and
        moving inboard toward the root (vector of length num_sections, m)

    Outputs
    -------
    c4sweep : float
        Average quarter chord sweep, weighted by section areas (scalar, deg)

    Options
    -------
    num_sections : int
        Number of spanwise sections to define planform shape (scalar, dimensionless)
    idx_sec_start : float
        Index in the inputs to begin the average sweep calculation (negative indices not
        accepted), by default 0
    idx_sec_end : float
        Index in the inputs to end the average sweep calculation (negative indices not
        accepted), by default num_sections - 1
    """

    def initialize(self):
        self.options.declare(
            "num_sections", default=2, types=int, desc="Number of sections along the half span to define"
        )
        self.options.declare("idx_sec_start", default=0)
        self.options.declare("idx_sec_end", default=None)

    def setup(self):
        self.n_sec = self.options["num_sections"]
        self.i_start = self.options["idx_sec_start"]
        self.i_end = self.options["idx_sec_end"]
        if self.i_end is None:
            self.i_end = self.n_sec
        else:
            self.i_end += 1  # make it exclusive

        self.add_input("x_LE_sec", shape=(self.n_sec,), units="m")
        self.add_input("y_sec", shape=(self.n_sec - 1,), units="m")
        self.add_input("chord_sec", shape=(self.n_sec,), units="m")

        self.add_output("c4sweep", units="deg")

        self.declare_partials("*", "*", method="cs")

    def compute(self, inputs, outputs):
        # Extract out the ones we care about
        LE_sec = inputs["x_LE_sec"][self.i_start : self.i_end]
        chord_sec = inputs["chord_sec"][self.i_start : self.i_end]
        y_sec = np.hstack((inputs["y_sec"], [0.0]))[self.i_start : self.i_end]

        # Compute the c4sweep for each section
        x_c4 = LE_sec + chord_sec * 0.25
        widths = y_sec[1:] - y_sec[:-1]  # section width in y direction
        setback = x_c4[:-1] - x_c4[1:]  # relative offset of sections in streamwise direction
        c4sweep_sec = np.arctan(setback / widths) * 180 / np.pi

        # Perform a weighted average with panel areas as weights
        A_sec = 0.5 * (chord_sec[:-1] + chord_sec[1:]) * widths
        outputs["c4sweep"] = np.sum(c4sweep_sec * A_sec) / np.sum(A_sec)
