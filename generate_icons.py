#!/usr/bin/env python3
"""
Generate PWA icons from a source image.
Usage: python generate_icons.py

Requirements:
    pip install Pillow

This script will generate icons in these sizes:
- 72x72, 96x96, 128x128, 144x144, 152x152, 192x192, 384x384, 512x512
"""

from PIL import Image
import os

# Icon sizes needed for PWA
SIZES = [72, 96, 128, 144, 152, 192, 384, 512]

# Source image (should be at least 512x512)
SOURCE_IMAGE = 'static/img/favicon.png'
OUTPUT_DIR = 'static/img/'

def generate_icons():
    """Generate all required icon sizes from source image."""
    
    if not os.path.exists(SOURCE_IMAGE):
        print(f"❌ Source image not found: {SOURCE_IMAGE}")
        print("Please place a high-resolution icon (512x512 or larger) at static/img/favicon.png")
        return False
    
    try:
        # Open source image
        img = Image.open(SOURCE_IMAGE)
        print(f"✅ Loaded source image: {img.size}")
        
        # Check if source is large enough
        if img.size[0] < 512 or img.size[1] < 512:
            print(f"⚠️  Warning: Source image is smaller than 512x512. Quality may be reduced.")
        
        # Ensure output directory exists
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        # Generate each size
        for size in SIZES:
            output_path = os.path.join(OUTPUT_DIR, f'icon-{size}x{size}.png')
            
            # Resize image maintaining aspect ratio
            resized = img.resize((size, size), Image.Resampling.LANCZOS)
            
            # Save as PNG
            resized.save(output_path, 'PNG', optimize=True)
            print(f"✅ Generated: {output_path}")
        
        print(f"\n✅ Successfully generated {len(SIZES)} icon sizes!")
        print("\nNext steps:")
        print("1. Review the generated icons in static/img/")
        print("2. Restart your Django development server")
        print("3. Visit your site and check browser console for PWA messages")
        print("4. Test installation on Android/iOS devices")
        
        return True
        
    except Exception as e:
        print(f"❌ Error generating icons: {e}")
        return False

if __name__ == '__main__':
    print("🎨 PWA Icon Generator")
    print("=" * 50)
    generate_icons()
