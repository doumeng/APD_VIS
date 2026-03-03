import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.simulator import UdpSimulator

if __name__ == "__main__":
    print("Starting Intensity/Range Simulator...")
    sim = UdpSimulator()
    try:
        sim.start(mode='int_rng', interval=0.02)
    except KeyboardInterrupt:
        print("Simulator stopped.")
