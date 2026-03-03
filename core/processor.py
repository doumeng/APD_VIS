import numpy as np
import time

try:
    import open3d as o3d
    HAS_OPEN3D = True
except ImportError:
    HAS_OPEN3D = False
    print("Warning: Open3D not found. DBSCAN denoising will be disabled.")

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False
    print("Warning: OpenCV not found. Using SciPy/NumPy fallbacks.")
    import scipy.ndimage

class ImageProcessor:
    def __init__(self):
        self.settings = {
            # Denoising
            'dbscan_enabled': False,
            'dbscan_eps': 0.05,
            'dbscan_min_points': 10,
            
            'range_gate_enabled': False,
            'range_min': 0.1,
            'range_max': 10.0,
            
            'intensity_filter_enabled': False,
            'min_intensity': 10,
            
            # Completion
            'completion_mode': 'none', # 'none', 'connected', 'morphological'
            'hole_size': 50,
            'morph_kernel': 3,
            
            'enabled': True
        }
        
        # Optimization: Pre-allocate point cloud if possible or reuse
        self.pcd = None
        if HAS_OPEN3D:
            self.pcd = o3d.geometry.PointCloud()

    def update_settings(self, settings_dict):
        """Update processing parameters."""
        self.settings.update(settings_dict)

    def process(self, intensity, rng):
        """
        Apply filters to intensity and range images.
        Input:
            intensity: 2D numpy array (128x128)
            rng: 2D numpy array (128x128) - likely in meters or units convertable
        Returns:
            (processed_intensity, processed_rng)
        """
        if not self.settings['enabled']:
            return intensity, rng

        proc_int = intensity.copy()
        proc_rng = rng.copy()

        # --- 1. Range Gating ---
        if self.settings['range_gate_enabled']:
            min_r = self.settings['range_min']
            max_r = self.settings['range_max']
            # Assume rng is in same units as min/max (e.g., meters)
            # If rng contains 0 for invalid, handle that.
            mask = (proc_rng < min_r) | (proc_rng > max_r)
            proc_rng[mask] = 0
            # Optional: Clear intensity where range is invalid?
            # proc_int[mask] = 0 

        # --- 2. Intensity Filter ---
        if self.settings['intensity_filter_enabled']:
            min_i = self.settings['min_intensity']
            mask = proc_int < min_i
            proc_rng[mask] = 0
            # Keep intensity as is or zero it out?
            # Usually we filter Range based on Intensity confidence.

        # --- 3. DBSCAN Denoising (Open3D) ---
        if self.settings['dbscan_enabled'] and HAS_OPEN3D:
            # Convert to Point Cloud
            # Generate (x, y, z) coordinates
            # This is slow if done every frame in Python.
            # Assuming rng is Z, and we have a grid.
            rows, cols = proc_rng.shape
            
            # Vectorized Point Cloud Generation
            # Create grid once if size is constant (optimization)
            # For now, do it on the fly
            
            valid_mask = proc_rng > 0
            z = proc_rng[valid_mask]
            if len(z) > 0:
                # Get indices
                y_idx, x_idx = np.where(valid_mask)
                
                # Simple conversion: x = x_idx * scale, y = y_idx * scale
                # Let's assume arbitrary scale 1 unit per pixel for X/Y to match Z units loosely
                # Or just treat X/Y as indices.
                # If Z is in meters (e.g. 5.0), and X is 0..127, scale mismatch might affect DBSCAN.
                # Usually we normalize or scale X/Y to real world if known.
                # Let's assume Z is typically 0-10000 (mm) or 0-10 (m).
                # If Z is mm, 128 pixels is small.
                # Let's assume Z is generic units.
                
                # To make DBSCAN work well, dimensions should be comparable.
                # Let's use simple index coordinates.
                points = np.stack((x_idx, y_idx, z), axis=-1).astype(np.float64)
                
                self.pcd.points = o3d.utility.Vector3dVector(points)
                
                # Run DBSCAN
                eps = self.settings['dbscan_eps']
                min_points = self.settings['dbscan_min_points']
                
                # DBSCAN returns labels: -1 is noise
                labels = np.array(self.pcd.cluster_dbscan(eps=eps, min_points=min_points, print_progress=False))
                
                # Identify noise points
                noise_mask_pcd = (labels == -1)
                
                # Map back to image
                # Points in 'points' correspond to 'valid_mask' locations
                # We need to zero out noise points in proc_rng
                
                # Get indices of noise points relative to the original image
                noise_x = x_idx[noise_mask_pcd]
                noise_y = y_idx[noise_mask_pcd]
                
                proc_rng[noise_y, noise_x] = 0

        # --- 4. Completion ---
        mode = self.settings['completion_mode']
        
        if mode == 'connected':
            hole_size = self.settings['hole_size']
            # Mask of valid pixels (assuming > 0 is valid)
            valid_mask = proc_rng > 0
            # Holes are !valid_mask (where value is 0 or NaN)
            hole_mask = ~valid_mask
            
            # Label connected components of holes
            # Using SciPy logic as default since CV2 might be missing or float compat issues
            # Scipy label works on boolean array
            labeled_array, num_features = scipy.ndimage.label(hole_mask)
            
            if num_features > 0:
                # Calculate sizes of each component
                # index 1..num_features
                sizes = scipy.ndimage.sum(hole_mask, labeled_array, index=np.arange(1, num_features + 1))
                
                # Identify small holes (size < threshold)
                small_holes_indices = np.where(sizes < hole_size)[0] + 1
                
                if len(small_holes_indices) > 0:
                    # Create mask of small holes to be filled
                    small_holes_mask = np.isin(labeled_array, small_holes_indices)
                    
                    # Fill logic:
                    # Simple approach: Replace small holes with maximum of local neighborhood (dilation)
                    # This effectively closes the hole with surrounding values.
                    # We can iterate dilation until hole is filled, or just once for speed.
                    # For real-time, one pass of a larger kernel or multiple passes of 3x3.
                    
                    # Let's use a 3x3 grey dilation on the valid pixels.
                    # To do this effectively:
                    # 1. Temporarily fill holes with 0 (done)
                    # 2. Compute max in neighborhood
                    # 3. If pixel is in small_holes_mask, replace with max.
                    # Note: If neighbors are also holes, max is 0. So we might need multiple passes or larger kernel.
                    # Better: 'grey_closing' is robust but modifies valid pixels too.
                    # We only want to modify 'small_holes_mask'.
                    
                    # Fast approximation: Maximum filter (3x3)
                    # We ignore 0s during max? No, standard max includes 0.
                    # So we need a "max of valid neighbors".
                    # This is tricky in pure numpy fast.
                    # Alternative: Use scipy.ndimage.grey_dilation but ensure 0 doesn't propagate if neighbors exist.
                    
                    # Let's try simple closing on the whole image, then apply ONLY to the mask.
                    closed_rng = scipy.ndimage.grey_closing(proc_rng, size=(3,3))
                    proc_rng[small_holes_mask] = closed_rng[small_holes_mask]

        elif mode == 'morphological':
            k_size = self.settings['morph_kernel']
            # Simple morphological closing
            proc_rng = scipy.ndimage.grey_closing(proc_rng, size=(k_size, k_size))

        return proc_int, proc_rng
