import sys
import os

# Add parent directory to path to find utils.simulator
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.simulator import UdpSimulator

if __name__ == "__main__":
    print("Starting ToF (Time of Flight) Simulator...")
    sim = UdpSimulator()
    try:
        sim.start(mode='tof', interval=0.02)
    except KeyboardInterrupt:
        print("Simulator stopped.")
