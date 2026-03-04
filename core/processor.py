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
    print("Warning: OpenCV not found. Completion algorithms will be disabled.")

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
                proc_int[noise_y, noise_x] = 0

        # --- 4. Completion ---
        if not HAS_CV2:
            return proc_int, proc_rng
            
        mode = self.settings['completion_mode']
        
        if mode == 'connected':
            hole_size = self.settings['hole_size']
            # Mask of holes (assuming <= 0 is hole/invalid)
            # proc_rng is float, usually 0 for invalid.
            hole_mask_uint8 = (proc_rng <= 0).astype(np.uint8)
            
            # Connected Components
            # connectivity=8 for 8-neighbor
            num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(hole_mask_uint8, connectivity=8)
            
            # stats: [x, y, width, height, area]
            # Label 0 is the background (valid pixels, since hole_mask has holes as 1)
            # Identify small holes (area < hole_size)
            # We skip label 0.
            
            if num_labels > 1:
                # stats[0] is background, stats[1:] are components
                # areas corresponding to labels 1..N-1
                areas = stats[1:, cv2.CC_STAT_AREA]
                
                # Get labels of components smaller than hole_size
                # np.where returns indices into 'areas', which is 0-based relative to stats[1:]
                # So actual label is index + 1
                small_hole_labels = np.where(areas < hole_size)[0] + 1
                
                if len(small_hole_labels) > 0:
                    # Create mask of small holes
                    # np.isin checks elements of 'labels' against 'small_hole_labels'
                    small_holes_mask = np.isin(labels, small_hole_labels)
                    
                    # Fill logic: Use inpaint for better hole filling
                    # mask needs to be uint8
                    inpaint_mask = small_holes_mask.astype(np.uint8)
                    
                    # Radius 3 pixels
                    proc_rng = cv2.inpaint(proc_rng, inpaint_mask, 3, cv2.INPAINT_TELEA)
                    proc_int = cv2.inpaint(proc_int, inpaint_mask, 3, cv2.INPAINT_TELEA)

        elif mode == 'morphological':
            k_size = int(self.settings['morph_kernel'])
            kernel = np.ones((k_size, k_size), np.uint8)
            
            # Dilate to fill holes
            dilated_rng = cv2.dilate(proc_rng, kernel, iterations=1)
            dilated_int = cv2.dilate(proc_int, kernel, iterations=1)
            
            # Only fill where original value was 0 (or invalid)
            # Assuming 0 is the hole value. 
            proc_rng = np.where(proc_rng <= 0, dilated_rng, proc_rng)
            proc_int = np.where(proc_int <= 0, dilated_int, proc_int)

        return proc_int, proc_rng
