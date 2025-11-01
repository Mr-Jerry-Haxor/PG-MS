# PWA (Progressive Web App) Setup Guide

## ✅ What's Been Done

Your PG-MS application is now a fully functional Progressive Web App! Here's what has been implemented:

### 1. **PWA Manifest** (`static/manifest.json`)
- App metadata (name, description, theme colors)
- Icon definitions for various screen sizes
- Display mode set to `standalone` for app-like experience
- Shortcuts for quick access to Dashboard and Tenants pages

### 2. **Service Worker** (`static/service-worker.js`)
- **Offline support** - App works even without internet
- **Smart caching strategy**:
  - Static assets (CSS, JS, images): Cache first
  - Dynamic content (API, pages): Network first with cache fallback
- **Auto-update** mechanism
- **Push notification** support (ready for future use)

### 3. **PWA Meta Tags** (Updated `base.html`)
- Theme color for Android/iOS
- Apple-specific meta tags for iOS
- Viewport settings optimized for mobile
- Manifest link

### 4. **Install Prompt**
- Custom install banner appears automatically
- One-click installation from browser
- Works on Android, iOS, and desktop

## 🚀 Setup Instructions

### Step 1: Generate App Icons

Run the icon generator script to create all required icon sizes:

```bash
# Activate your virtual environment first
& D:/GITHUB/PG-MS/.venv/Scripts/Activate.ps1

# Install Pillow if not already installed
pip install Pillow

# Generate icons from your favicon.png
python generate_icons.py
```

This will create icons in these sizes: 72x72, 96x96, 128x128, 144x144, 152x152, 192x192, 384x384, 512x512

**Note:** If your `favicon.png` is small, consider replacing it with a higher resolution image (512x512 recommended) before running the generator.

### Step 2: Collect Static Files (Production)

If you're deploying to production, collect static files:

```bash
python manage.py collectstatic
```

### Step 3: Configure URLs (Optional)

Add a route to serve the service worker from root (recommended for better caching):

In your main `urls.py`:
```python
from django.views.static import serve

urlpatterns = [
    # ... existing patterns ...
    path('service-worker.js', serve, {
        'document_root': settings.STATIC_ROOT,
        'path': 'service-worker.js'
    }),
]
```

### Step 4: HTTPS Requirement

**Important:** PWAs require HTTPS in production!
- Service workers only work over HTTPS (except on localhost)
- Install SSL certificate on your production server
- Or use services like Cloudflare for free SSL

## 📱 Testing on Mobile Devices

### Android (Chrome)

1. Open your site in Chrome on Android
2. You should see an "Install app" banner automatically
3. **Or** tap the 3-dot menu → "Install app" or "Add to Home screen"
4. The app icon will appear on your home screen
5. Launch it - it will open in standalone mode (no browser UI)

**To test:**
- Put phone in airplane mode
- Open the installed app
- Most pages should still work (cached content)

### iOS (Safari)

1. Open your site in Safari on iPhone/iPad
2. Tap the Share button (square with arrow)
3. Scroll down and tap "Add to Home Screen"
4. Tap "Add" in the top right
5. The app icon will appear on your home screen

**iOS Limitations:**
- iOS doesn't show automatic install prompts
- Users must manually add via Share menu
- Service worker support is good but slightly limited compared to Android

### Desktop (Chrome/Edge)

1. Open your site in Chrome or Edge
2. Look for install icon in address bar (⊕ or ⋮)
3. Click "Install PG-MS"
4. App opens in its own window

## 🧪 Testing Offline Functionality

1. Open your PWA in a browser
2. Open DevTools (F12)
3. Go to **Application** tab
4. Check "Service Workers" section - should show "Activated and running"
5. Go to **Network** tab
6. Check "Offline" checkbox to simulate no internet
7. Navigate through the app - cached pages should still load

## 🔍 Debugging

### Check Service Worker Registration

Open browser console and look for:
```
✅ Service Worker registered successfully: <scope>
```

### View Cached Assets

1. Open DevTools → Application tab
2. Expand "Cache Storage"
3. Click on "pgms-cache-v1.0.0"
4. See all cached resources

### Common Issues

**Service worker not registering:**
- Check browser console for errors
- Ensure you're on HTTPS (or localhost)
- Clear browser cache and reload

**Icons not showing:**
- Run `python generate_icons.py` to create icons
- Check that icons exist in `static/img/`
- Clear cache and reinstall app

**App not going offline:**
- Check Network tab in DevTools
- Ensure service worker is "Activated"
- Try visiting pages while online first (to cache them)

## 🎨 Customization

### Change App Colors

Edit `static/manifest.json`:
```json
{
  "theme_color": "#2563eb",  // Browser address bar color
  "background_color": "#f5f7fb"  // Splash screen background
}
```

Also update `<meta name="theme-color">` in `base.html`.

### Add More Shortcuts

Edit `static/manifest.json` shortcuts array:
```json
{
  "shortcuts": [
    {
      "name": "View Bookings",
      "url": "/pg/bookings/",
      "icons": [{"src": "/static/img/icon-192x192.png", "sizes": "192x192"}]
    }
  ]
}
```

### Update Cache Version

When deploying updates, increment version in `static/service-worker.js`:
```javascript
const CACHE_VERSION = 'pgms-v1.0.1';  // Increment this
```

## 📊 PWA Features Checklist

- ✅ Web App Manifest
- ✅ Service Worker with offline support
- ✅ HTTPS ready (required for production)
- ✅ Responsive design (mobile-first)
- ✅ App icons (all sizes)
- ✅ Theme colors
- ✅ Install prompts
- ✅ Splash screen support
- ✅ Standalone display mode
- ✅ Cache strategies (static + dynamic)
- ✅ Auto-update mechanism
- ✅ iOS Safari support
- ✅ Android Chrome support
- ✅ Desktop installation support

## 🚀 Production Deployment

1. **Set up HTTPS** on your server (required!)
2. Run `python generate_icons.py` to create all icons
3. Run `python manage.py collectstatic` to collect static files
4. Update `CACHE_VERSION` in service-worker.js for each deployment
5. Test on real devices (Android & iOS)
6. Submit to app stores (optional - PWAs can be submitted to Google Play and Microsoft Store)

## 📱 PWA vs Native App

**Advantages:**
- ✅ No app store approval needed
- ✅ Instant updates (no user action required)
- ✅ One codebase for all platforms
- ✅ Smaller download size
- ✅ Works offline
- ✅ Can be submitted to app stores (optional)

**Limitations:**
- ❌ No access to some native APIs (Bluetooth, NFC, etc.)
- ❌ iOS has some PWA limitations
- ❌ Can't run in background like native apps
- ❌ Slightly different UI chrome on each platform

## 🎯 Next Steps

1. **Generate icons**: `python generate_icons.py`
2. **Test locally**: Visit site on mobile devices connected to same network
3. **Deploy to production** with HTTPS
4. **Test installation** on real Android and iOS devices
5. **Monitor usage** via browser DevTools and analytics

## 💡 Tips

- Always test on real devices, not just emulators
- Clear browser cache when testing updates
- Use Chrome DevTools "Application" tab for debugging
- Check Lighthouse PWA score: DevTools → Lighthouse → PWA audit
- Consider adding push notifications for important updates

## 📚 Resources

- [MDN PWA Guide](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps)
- [web.dev PWA](https://web.dev/progressive-web-apps/)
- [PWA Builder](https://www.pwabuilder.com/)
- [Chrome DevTools PWA](https://developer.chrome.com/docs/devtools/progressive-web-apps/)

---

**Need Help?**
- Check browser console for errors
- Use Chrome DevTools Application tab to debug service worker
- Test offline mode in DevTools Network tab
- Verify all icons exist in static/img/ directory
