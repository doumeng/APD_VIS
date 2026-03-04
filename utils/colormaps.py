import numpy as np
import pyqtgraph as pg

def register_custom_cmaps():
    """
    Register 'jet' colormap if not available in pyqtgraph.
    Uses matplotlib to generate the lookup table.
    """
    if 'jet' in pg.colormap.listMaps():
        return

    try:
        import matplotlib.cm
        
        # Get 'jet' colormap from matplotlib
        cmap = matplotlib.cm.get_cmap('jet')
        
        # Generate lookup table (256 colors)
        # matplotlib returns RGBA in 0-1 range
        lut = cmap(np.linspace(0, 1, 256))
        
        # Convert to 0-255 uint8
        lut = (lut * 255).astype(np.uint8)
        
        # Register with pyqtgraph
        # pg.ColorMap takes (pos, color)
        # Or we can just add it directly if we have the object.
        # Wait, pg.colormap.add takes a ColorMap object.
        
        # Create ColorMap object from lookup table
        # We need positions for the colors.
        pos = np.linspace(0, 1, 256)
        colors = lut
        
        # pg.ColorMap(pos, color) expects color as (R,G,B,A) usually.
        # But 'lut' is (256, 4).
        
        # Let's use simpler approach: Just add it to the global dict if possible?
        # pg.colormap.register(name, colormap) in newer versions?
        # In older versions, it might be tricky.
        
        # Let's construct a ColorMap and add it.
        cm = pg.ColorMap(pos, colors)
        
        # Add to global list?
        # pg.graphicsItems.GradientEditorItem.Gradients is a dict of definitions.
        # But pg.colormap.get() uses a different registry.
        
        # Actually, let's just make sure we can get it.
        # There isn't a simple 'register' function in older pyqtgraph versions that works globally for 'get'.
        # However, we can use a trick: 
        # But wait, if I use pg.colormap.get('jet'), it looks in some internal list.
        
        # Let's check if there is a way to add to the list.
        # Standard way: pg.colormap.register(name, source) isn't standard.
        
        # Alternative: Return the colormap object and use it directly.
        pass
    except ImportError:
        print("Warning: Matplotlib not found, 'jet' colormap unavailable.")
    except Exception as e:
        print(f"Error registering 'jet': {e}")

def get_colormap(name):
    """
    Safe wrapper for pg.colormap.get
    """
    try:
        return pg.colormap.get(name)
    except Exception:
        # Fallback for 'jet'
        if name == 'jet':
            try:
                import matplotlib.cm
                cmap = matplotlib.cm.get_cmap('jet')
                lut = cmap(np.linspace(0, 1, 256))
                lut = (lut * 255).astype(np.uint8)
                pos = np.linspace(0, 1, 256)
                return pg.ColorMap(pos, lut)
            except:
                print("Fallback: Using viridis instead of jet")
                return pg.colormap.get('viridis')
        return pg.colormap.get('viridis')
