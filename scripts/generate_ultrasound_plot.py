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

logger = logging.getLogger(__name__)
ch = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)

def generate_ultrasound_plot_from_solution(solution, mode="file"):
    plt.style.use('dark_background')
    fig, ax = plt.subplots(3, 1, figsize=(7.5, 6.5), gridspec_kw={'height_ratios': [1, 1, 3], 'hspace': 0.35})
    fig.set_facecolor('#1E1E20')  # Match QML dark theme background
    pulse = solution["pulse"]
    delays = np.array(solution["delays"])
    apodizations = np.array(solution["apodizations"])
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
    ax[0].plot(pulse_t * 1e6, pulse_waveform_tristate,'.:')
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
    pulse_train_waveform_posenv = np.zeros_like(pulse_train_t) + A/100
    pulse_train_waveform_posenv[((pulse_train_t % pulse_interval) < pulse["duration"]) & (pulse_train_t < pulse_train_length)] = A
    pulse_train_waveform_negenv = np.zeros_like(pulse_train_t) - A/100
    pulse_train_waveform_negenv[((pulse_train_t % pulse_interval) < pulse["duration"]) & (pulse_train_t < pulse_train_length)] = -A
    ax[1].fill_between(pulse_train_t * 1e3, pulse_train_waveform_posenv, pulse_train_waveform_negenv, alpha=1.0)
    #ax[1].set_title("Pulse Train Envelope")
    ax[1].set_xlabel("Time (ms)")
    ax[1].set_ylabel("Amplitude (V)")
    ax[1].set_ylim(-A*1.5, A*1.5)
    ax[1].legend(["Pulse Train Envelope"], loc="upper right")

    if 'elements' in transducer:
        element_positions = np.array([elem.get('position', [0, 0, 0]) for elem in transducer['elements']])
        ax[2].scatter(element_positions[:, 0], element_positions[:, 1], c=delays, marker='s', s=apodizations*100, cmap='turbo', edgecolors='white')
        ax[2].set_xlabel("X (mm)")
        ax[2].set_ylabel("Y (mm)")
        ax[2].set_aspect('equal', adjustable='box')
        xlim = [np.min(element_positions[:, 0]) - 5, np.max(element_positions[:, 0]) + 5]
        ylim = [np.min(element_positions[:, 1]) - 5, np.max(element_positions[:, 1]) + 5]
        ax[2].set_xlim(xlim)
        ax[2].set_ylim(ylim)

    if mode == "file":
            # Save plot as file
            output_path = os.path.abspath("generated_plot.png")
            fig.savefig(output_path, dpi=100, bbox_inches='tight')
            plt.close(fig)
            return output_path + f"?v={int(time.time())}"
    elif mode == "buffer":
        # Save to a BytesIO buffer instead of a file
        buffer = BytesIO()
        fig.savefig(buffer, format="png", dpi=100, bbox_inches='tight')
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
