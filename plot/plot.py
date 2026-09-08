import numpy as np
import matplotlib.pyplot as plt
from scipy.special import j1
import sys
import os
import time
import base64
from io import BytesIO
import logging

# ✅ Fix: Set the non-GUI backend before using matplotlib
import matplotlib
matplotlib.use("Agg")  # Prevents QWidget errors
from matplotlib.colors import to_hex
from matplotlib.ticker import MaxNLocator

logger = logging.getLogger(__name__)

# Focus colour cycle. tab20 rather than tab10, because the transmitter has
# 16 profile slots and a 10-colour cycle would repeat foci 11-16. tab20
# alternates a dark and a light variant of each hue, so the darks are taken
# first to keep neighbouring foci clearly apart.

# Fitted-box right edge past which the element map is left-aligned instead
# of centred. 2x reaches ~0.934, 1x only ~0.726.
ELEMENT_MAP_LEFT_ALIGN_ABOVE = 0.85

# Slot fraction for a wide (2x) map, so it clears the delay-profile
# selector. Smallest window that clears: 1.00 -> 1150 px, 0.82 -> 1100,
# 0.74 -> 1050.
WIDE_ARRAY_MAP_SHRINK = 0.74

# Marker area in points^2: 100 * (750/660)^2, compensating for the fixed
# 750 px render vs the ~660 px the old tight crop produced.
ELEMENT_MARKER_AREA = 129

PROFILE_COLORMAP = "tab20"
_PROFILE_COLOR_ORDER = list(range(0, 20, 2)) + list(range(1, 20, 2))
PROFILE_COLOR_COUNT = len(_PROFILE_COLOR_ORDER)


def profile_color(index):
    """RGBA colour for the 0-based delay-profile *index*."""
    cmap = plt.get_cmap(PROFILE_COLORMAP)
    return cmap(_PROFILE_COLOR_ORDER[int(index) % PROFILE_COLOR_COUNT])


def profile_color_hex(count):
    """The first *count* profile colours as '#rrggbb' strings.

    Exposed to QML through the connector, so the focus dropdown's swatches
    match the plot markers by construction rather than by a hand-copied
    palette that would drift if the colormap changed.
    """
    return [to_hex(profile_color(i)) for i in range(max(0, int(count)))]


def generate_ultrasound_plot_from_solution(solution, mode="file", focus_index=0):
    """Render the pulse / pulse-train / element-map figure for *solution*.

    *focus_index* is the 0-based delay profile drawn on the element map and
    emphasised in the pulse-train envelope. Every focus stays visible in
    both panels, so the selection only decides which is shown in full
    detail. Out-of-range values are clamped rather than raising, so a stale
    UI selection still renders.
    """
    plt.style.use('dark_background')
    fig, ax = plt.subplots(3, 1, figsize=(7.5, 6.5), gridspec_kw={'height_ratios': [1, 1, 3], 'hspace': 0.35})
    fig.set_facecolor('#1E1E20')  # Match QML dark theme background
    # Replaces bbox_inches='tight', which cropped to content and so let the
    # map's height change the image aspect and shift the pulse panels.
    fig.subplots_adjust(left=0.10, right=0.98, top=0.96, bottom=0.08, hspace=0.35)
    pulse = solution["pulse"]
    # Multi-focus solutions carry one delay/apodization row per focus.
    # Normalize to 2-D so the single- and multi-focus paths share code.
    delays = np.atleast_2d(np.array(solution["delays"]))
    apodizations = np.atleast_2d(np.array(solution["apodizations"]))
    n_profiles = delays.shape[0]
    try:
        focus_index = int(focus_index)
    except (TypeError, ValueError):
        focus_index = 0
    focus_index = min(max(focus_index, 0), n_profiles - 1)
    # Apodization rows are only ever fewer than delay rows in a malformed
    # solution. Clamp separately so a bad file still renders.
    apod_index = min(focus_index, apodizations.shape[0] - 1)
    execution_order = list(solution.get("execution_order") or range(1, n_profiles + 1))
    transducer = solution.get('transducer', {})
    sequence = solution["sequence"]
    voltage = solution["voltage"]
    #ppw = 24  # Points per wavelength
    #pulse_dt = 1 / (pulse["frequency"] * ppw)
    pulse_dt = 1/10e6
    pulse_t = np.arange(0, pulse["duration"], pulse_dt)
    A = pulse['amplitude'] * voltage
    pulse_waveform = A * np.sin(2 * np.pi * pulse["frequency"] * pulse_t + np.pi/6)
    pulse_waveform_tristate = np.where(pulse_waveform > A/2, A, np.where(pulse_waveform < -A/2, -A, 0))
    ax[0].plot(pulse_t * 1e6, pulse_waveform_tristate,'-')
    #ax[0].plot(pulse_t * 1e6, pulse_waveform)
    #ax[0].set_title("Single Pulse Waveform")
    ax[0].set_xlabel("Time (µs)")
    ax[0].set_ylim(-A*1.5, A*1.5)
    ax[0].set_ylabel("Amplitude (V)")
    ax[0].legend(["Pulse"], loc="upper right")
    ppp = 24
    pulse_train_dt = pulse["duration"]/ppp
    pulse_interval = max(pulse["duration"], sequence['pulse_interval'])
    pulse_train_length = sequence['pulse_count'] * pulse_interval
    pulse_train_interval = max(pulse_train_length, sequence['pulse_train_interval'])
    pulse_train_t = np.arange(0, pulse_train_interval, pulse_train_dt)
    firing = ((pulse_train_t % pulse_interval) < pulse["duration"]) & (pulse_train_t < pulse_train_length)
    pulse_train_waveform_posenv = np.zeros_like(pulse_train_t) + A/100
    pulse_train_waveform_posenv[firing] = A
    pulse_train_waveform_negenv = np.zeros_like(pulse_train_t) - A/100
    pulse_train_waveform_negenv[firing] = -A

    if n_profiles > 1:
        # Colour each pulse by the focus the firmware fires it at. The
        # execution order is walked at pulse boundaries, giving each entry
        # pulse_count/len(order) consecutive pulses, and restarts at the
        # top of every pulse train.
        cycle_length = len(execution_order)
        pulses_per_entry = max(1, int(sequence['pulse_count']) // cycle_length)
        pulse_index = np.floor(pulse_train_t / pulse_interval).astype(int)
        order_index = (pulse_index // pulses_per_entry) % cycle_length
        profile_at_t = np.take(np.array(execution_order), order_index)
        # Baseline (between pulses) drawn once so the gaps stay visible.
        ax[1].fill_between(pulse_train_t * 1e3,
                           np.full_like(pulse_train_t, A/100),
                           np.full_like(pulse_train_t, -A/100),
                           color="#888888", alpha=1.0)
        # No legend. At 16 profiles it covers the whole panel, hiding the
        # pulses it labels. Instead the selected focus keeps its colour and
        # everything else goes grey, so the panel shows where that focus
        # fires in the train.
        selected_profile = focus_index + 1
        selected_color = profile_color(selected_profile - 1)

        other_mask = firing & (profile_at_t != selected_profile)
        if other_mask.any():
            ax[1].fill_between(pulse_train_t * 1e3,
                               pulse_train_waveform_posenv,
                               pulse_train_waveform_negenv,
                               where=other_mask, color="#6E7480")
        selected_mask = firing & (profile_at_t == selected_profile)
        if selected_mask.any():
            ax[1].fill_between(pulse_train_t * 1e3,
                               pulse_train_waveform_posenv,
                               pulse_train_waveform_negenv,
                               where=selected_mask, color=selected_color)

        # A pulse lasts microseconds on a millisecond axis, so each bar is
        # sub-pixel wide and colour alone is invisible. Mark the selected
        # focus's pulses instead, up to the point where the markers would
        # merge into a smear.
        total_pulses = int(sequence['pulse_count'])
        selected_times_ms = [
            k * pulse_interval * 1e3
            for k in range(total_pulses)
            if execution_order[(k // pulses_per_entry) % cycle_length] == selected_profile
        ]
        if 0 < len(selected_times_ms) <= 64:
            ax[1].plot(selected_times_ms, [A * 1.22] * len(selected_times_ms),
                       linestyle='none', marker='v', markersize=5,
                       markeredgecolor='white', markeredgewidth=0.5,
                       color=selected_color)
    else:
        ax[1].fill_between(pulse_train_t * 1e3, pulse_train_waveform_posenv, pulse_train_waveform_negenv, alpha=1.0)
        ax[1].legend(["Pulse Train Envelope"], loc="upper right")
    #ax[1].set_title("Pulse Train Envelope")
    ax[1].set_xlabel("Time (ms)")
    ax[1].set_ylabel("Amplitude (V)")
    ax[1].set_ylim(-A*1.5, A*1.5)

    if 'elements' in transducer:
        element_positions = np.array([elem.get('position', [0, 0, 0]) for elem in transducer['elements']])
        # The map can only show one delay pattern at a time. Draw the
        # selected profile and mark every focus, so the raster stays legible.
        ax[2].scatter(element_positions[:, 0], element_positions[:, 1], c=delays[focus_index], marker='s', s=apodizations[apod_index]*ELEMENT_MARKER_AREA, cmap='turbo', edgecolors='white')
        ax[2].set_xlabel("X (mm)")
        ax[2].set_ylabel("Y (mm)")
        ax[2].set_aspect('equal', adjustable='box')
        xs = [np.min(element_positions[:, 0]) - 5, np.max(element_positions[:, 0]) + 5]
        ys = [np.min(element_positions[:, 1]) - 5, np.max(element_positions[:, 1]) + 5]

        focus_positions = [entry.get('position') for entry in (solution.get('foci') or [])
                           if isinstance(entry, dict) and entry.get('position') is not None]
        if len(focus_positions) > 1:
            # Foci are identified by colour alone, matching the UI's focus
            # dropdown. Numeric labels collided with each other and with the
            # element grid at close focus spacing.
            for index, position in enumerate(focus_positions, start=1):
                is_selected = index == focus_index + 1
                ax[2].plot(position[0], position[1], marker='x',
                           markersize=15 if is_selected else 9,
                           markeredgewidth=3 if is_selected else 2,
                           alpha=1.0 if is_selected else 0.7,
                           color=profile_color(index - 1))
                if is_selected:
                    # A ring around the focus whose delays are on screen, so
                    # the selection is readable even where foci overlap.
                    ax[2].plot(position[0], position[1], marker='o', markersize=20,
                               markerfacecolor='none', markeredgewidth=1.5,
                               color=profile_color(index - 1))
                xs = [min(xs[0], position[0] - 5), max(xs[1], position[0] + 5)]
                ys = [min(ys[0], position[1] - 5), max(ys[1], position[1] + 5)]

        ax[2].set_xlim(xs)
        ax[2].set_ylim(ys)
        # Keep 10 mm steps. The roomier axes makes the locator pick 5 mm.
        ax[2].yaxis.set_major_locator(MaxNLocator(nbins=5, steps=[1, 2, 5, 10]))

        # Move a wide map off the delay-profile selector. Keyed on the fitted
        # box, so it tracks the array rather than the focus count. The slot
        # must come from the gridspec, because an equal-aspect axes has its
        # position rewritten to the fitted box.
        slot = ax[2].get_subplotspec().get_position(fig)
        ax[2].apply_aspect()
        if ax[2].get_position(original=False).x1 > ELEMENT_MAP_LEFT_ALIGN_ABOVE:
            # Left-aligning alone only reclaims the centring slack, which is
            # not enough at smaller windows, so scale the map down as well.
            ax[2].set_position([slot.x0, slot.y0,
                                slot.width * WIDE_ARRAY_MAP_SHRINK, slot.height])
            ax[2].set_anchor('W')

    if mode == "file":
            # Save plot as file
            output_path = os.path.abspath("generated_plot.png")
            # Fixed-size save: a tight crop would let the map's height
            # change the image aspect and move the pulse panels.
            fig.savefig(output_path, dpi=100)
            plt.close(fig)
            return output_path + f"?v={int(time.time())}"
    elif mode == "buffer":
        # Save to a BytesIO buffer instead of a file
        buffer = BytesIO()
        # Fixed-size render. See the file-mode save above.
        fig.savefig(buffer, format="png", dpi=100)
        plt.close(fig)
        
        # Encode image in Base64
        buffer.seek(0)
        base64_image = base64.b64encode(buffer.getvalue()).decode("utf-8")
        return base64_image


def generate_ultrasound_plot(x_focus, y_focus, z_focus, frequency, cycles, trigger, mode="file"):
    try:
        # Convert input values
        x_focus = float(x_focus)
        y_focus = float(y_focus)
        z_focus = float(z_focus)
        frequency = float(frequency)
        cycles = int(cycles)
        trigger = float(trigger)

        # Constants
        wavelength = 1500 / frequency  # Speed of sound in tissue ~1500 m/s
        beam_width = 5  # Beam width in mm

        # Generate grid
        x = np.linspace(-20, 20, 100)
        z = np.linspace(0, 100, 100)
        X_grid, Z_grid = np.meshgrid(x, z)

        # Compute beam intensity using Gaussian approximation
        r = np.sqrt((X_grid - x_focus)**2)
        z_rel = Z_grid - z_focus

        # Bessel-Gaussian Beam Profile
        with np.errstate(divide='ignore', invalid='ignore'):  # Avoid warnings
            bessel_term = j1(2 * np.pi * r / beam_width) / (2 * np.pi * r / beam_width)
            bessel_term[r == 0] = 0.5  # Handling singularity at r = 0

        intensity = (bessel_term**2) * np.exp(-((z_rel / beam_width)**2))
        intensity /= np.max(intensity)
        intensity[intensity < 0.01] = np.nan  # Apply threshold to enhance visibility

        # Create plot
        fig, ax = plt.subplots(figsize=(10, 6))
        c = ax.contourf(X_grid, Z_grid, intensity, levels=50, cmap='plasma')
        plt.colorbar(c, label='Normalized Intensity')
        ax.set_xlabel("X (mm)")
        ax.set_ylabel("Z (mm)")
        ax.set_title("Focused Ultrasound Beam 2D Profile")

        if mode == "file":
            # Save plot as file
            output_path = os.path.abspath("generated_plot.png")
            plt.savefig(output_path, dpi=100, bbox_inches='tight')
            plt.close()
            return output_path + f"?v={int(time.time())}"

        elif mode == "buffer":
            # Save to a BytesIO buffer instead of a file
            buffer = BytesIO()
            plt.savefig(buffer, format="png", dpi=100, bbox_inches='tight')
            plt.close()
            
            # Encode image in Base64
            buffer.seek(0)
            base64_image = base64.b64encode(buffer.getvalue()).decode("utf-8")
            return base64_image

    except Exception as e:
        logger.error(f"Error generating ultrasound plot: {e}", file=sys.stderr)
        return "ERROR"

# If running as script
if __name__ == "__main__":
    if len(sys.argv) < 7:
        logger.error("ERROR: Not enough arguments provided", file=sys.stderr)
        sys.exit(1)

    x, y, z, freq, cycles, trigger = sys.argv[1:7]
    mode = sys.argv[7] if len(sys.argv) > 7 else "file"  # Default to file mode
    output = generate_ultrasound_plot(x, y, z, freq, cycles, trigger, mode)
    logger.info(output)  # Print Base64 image or file path
