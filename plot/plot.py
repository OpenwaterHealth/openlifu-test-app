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

# Focus/delay-profile colour cycle.
#
# tab20 rather than tab10 because the transmitter has 16 delay-profile
# slots: a 10-colour cycle gives foci 11-16 the same colours as 1-6, and
# colour is the only thing identifying a focus on the element map.
#
# tab20 interleaves a dark and a light variant of each tab10 hue (dark
# blue, light blue, dark orange, light orange, ...), so consecutive
# entries share a hue. Taking the darks first keeps foci 1-10 on the
# well-separated tab10 hues and pushes the lighter variants to 11-20 --
# neighbouring foci never share a hue, and the two variants of one hue are
# always 10 apart.

# Right edge, as a fraction of figure width, past which the element map is
# left-aligned in its slot rather than centred. A 2x array's map reaches
# ~0.934 and needs the shift; a 1x array's reaches ~0.726 and does not.
ELEMENT_MAP_LEFT_ALIGN_ABOVE = 0.85

# How much of its slot a wide (2x) element map gets. Applied only above the
# threshold above, so a 1x array is untouched.
#
# Measured against the UI -- the smallest window at which the map clears the
# delay-profile selector: 1.00 -> 1150 px, 0.88 -> 1150, 0.82 -> 1100,
# 0.78 -> 1100, 0.74 -> 1050. (Above ~0.9 the map is limited by the panel
# height, not the slot width, so narrowing the slot changes nothing.)
WIDE_ARRAY_MAP_SHRINK = 0.74

# Element marker area, in points^2. Scaled up from the original 100 by
# (750/660)^2: dropping bbox_inches='tight' made the render 750 px wide
# instead of the ~660 px the crop produced, so the UI now scales it down
# further to fit the same panel. Without this the squares came out ~12%
# smaller on screen.
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

    Exposed to QML (via the connector) so the focus dropdown's swatches are
    the same colours as the plot markers by construction, rather than by a
    hand-copied palette that silently drifts if the colormap changes.
    """
    return [to_hex(profile_color(i)) for i in range(max(0, int(count)))]


def generate_ultrasound_plot_from_solution(solution, mode="file", focus_index=0):
    """Render the pulse / pulse-train / element-map figure for *solution*.

    *focus_index* is the 0-based delay profile whose per-element delays are
    drawn on the element map, and which is emphasised in the pulse-train
    envelope. Every focus stays visible in both panels regardless -- the
    selection only decides which one is shown in full detail. Out-of-range
    values are clamped rather than raising, so a stale UI selection (e.g.
    after foci were removed) still renders.
    """
    plt.style.use('dark_background')
    fig, ax = plt.subplots(3, 1, figsize=(7.5, 6.5), gridspec_kw={'height_ratios': [1, 1, 3], 'hspace': 0.35})
    fig.set_facecolor('#1E1E20')  # Match QML dark theme background
    # Trim the default margins so the panels fill the figure. This replaces
    # what bbox_inches='tight' used to do at save time -- that cropped to
    # content, which made the image's aspect depend on how tall the element
    # map happened to be, and so moved the pulse panels whenever the map
    # changed. Doing it here instead keeps the render a fixed size.
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
    # Apodization rows can be fewer than delay rows only in malformed
    # solutions; clamp separately so a bad file degrades to a plot rather
    # than an exception.
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
        # No legend here. At the 16-profile maximum it needs four rows of
        # entries and covers the whole panel, hiding the very pulses it
        # labels. This panel answers one question instead -- "where in the
        # train does the focus I picked fire?" -- so everything else is
        # drawn in one muted grey and the selection gets the colour. That
        # colour is named by the swatch in the UI's focus dropdown, which
        # is what drives the selection in the first place.
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

        # A pulse lasts microseconds on an axis measured in milliseconds, so
        # each bar is sub-pixel wide and colour alone cannot be seen. Tag the
        # selected focus's pulses with a marker above the envelope. Skipped
        # past a threshold where the markers would merge into a smear and
        # stop being a highlight.
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
        # The element map can only show one delay pattern at a time; draw
        # the selected profile and mark every focus so the raster pattern
        # stays legible in the same view.
        ax[2].scatter(element_positions[:, 0], element_positions[:, 1], c=delays[focus_index], marker='s', s=apodizations[apod_index]*ELEMENT_MARKER_AREA, cmap='turbo', edgecolors='white')
        ax[2].set_xlabel("X (mm)")
        ax[2].set_ylabel("Y (mm)")
        ax[2].set_aspect('equal', adjustable='box')
        xs = [np.min(element_positions[:, 0]) - 5, np.max(element_positions[:, 0]) + 5]
        ys = [np.min(element_positions[:, 1]) - 5, np.max(element_positions[:, 1]) + 5]

        focus_positions = [entry.get('position') for entry in (solution.get('foci') or [])
                           if isinstance(entry, dict) and entry.get('position') is not None]
        if len(focus_positions) > 1:
            # Foci are identified by colour alone -- the same tab10 sequence
            # the pulse-train legend and the UI's focus dropdown use. No
            # numeric annotations: at close focus spacing the labels collide
            # with each other and with the element grid.
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
        # Cap the tick count: the trimmed figure margins gave this axes more
        # room, and the automatic locator answered with 5 mm steps where it
        # used to use 10. Bin it back down to the coarser spacing.
        ax[2].yaxis.set_major_locator(MaxNLocator(nbins=5, steps=[1, 2, 5, 10]))

        # An equal-aspect axes is fitted inside its slot and centred, leaving
        # slack on both sides. For a wide array (2x) that centring pushes the
        # map's right edge under the UI's delay-profile selector, so hand the
        # slack to the left instead: same map, same size, just shifted across.
        #
        # Only for maps wide enough to have the problem -- a 1x array is
        # narrow, sits nowhere near the selector, and left-aligning it would
        # strand it against the edge with a half-empty panel. The threshold is
        # on the fitted box, so it depends on the array, never on the focus
        # count: the plot is identical whether there is one focus or sixteen.
        # The slot comes from the gridspec, not from get_position(): an
        # equal-aspect axes has its position rewritten to the fitted box, so
        # get_position() returns a box already shrunk to the data aspect.
        # Feeding that back in would pin the map where it is and make the
        # left-align below a no-op.
        slot = ax[2].get_subplotspec().get_position(fig)
        ax[2].apply_aspect()
        if ax[2].get_position(original=False).x1 > ELEMENT_MAP_LEFT_ALIGN_ABOVE:
            # ...and give it a narrower slot as well. Left-aligning alone
            # only reclaims the centring slack, which is not enough to clear
            # the selector at smaller window sizes. Narrowing the slot with
            # the aspect locked scales the map down as a whole.
            ax[2].set_position([slot.x0, slot.y0,
                                slot.width * WIDE_ARRAY_MAP_SHRINK, slot.height])
            ax[2].set_anchor('W')

    if mode == "file":
            # Save plot as file
            output_path = os.path.abspath("generated_plot.png")
            # No bbox_inches='tight': it crops to content, so shrinking the
            # element map for a multi-focus solution also shortened the crop,
            # changing the image's aspect and sliding the pulse panels on
            # screen. A fixed figure-sized save keeps every panel put.
            fig.savefig(output_path, dpi=100)
            plt.close(fig)
            return output_path + f"?v={int(time.time())}"
    elif mode == "buffer":
        # Save to a BytesIO buffer instead of a file
        buffer = BytesIO()
        # See the note on the file-mode save above: a fixed-size render is
        # what keeps the pulse panels from moving between focus counts.
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
