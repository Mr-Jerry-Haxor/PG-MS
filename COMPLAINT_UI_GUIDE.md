# 🎨 Complaint System - UI/UX Visual Guide

## 📱 Mobile-First Design Philosophy

All interfaces are designed **mobile-first** and progressively enhanced for larger screens.

---

## 👤 TENANT INTERFACE

### 1️⃣ My Complaints Page
**URL**: `/accounts/complaints/`

```
┌─────────────────────────────────────┐
│  📱 My Complaints                   │
│  Track and manage your issues       │
│                [+ New Complaint]    │
├─────────────────────────────────────┤
│  Filters ▼                          │
│  ┌────────────┬──────────┐          │
│  │ Status     │ All PGs  │          │
│  └────────────┴──────────┘          │
├─────────────────────────────────────┤
│  ┌───────────────────────────────┐  │
│  │ 🔧 WiFi Not Working           │  │
│  │ [🔴 Open] [🔴 Urgent]         │  │
│  │ Internet down for 2 days...   │  │
│  │ 🏢 Green Valley PG • 📅 Nov 10│  │
│  │ 💬 2 comments            →    │  │
│  └───────────────────────────────┘  │
│  ┌───────────────────────────────┐  │
│  │ 🧹 Room Cleanliness Issue     │  │
│  │ [🟡 In Progress] [🔵 Medium] │  │
│  │ Bathroom needs attention...   │  │
│  │ 🏢 Green Valley PG • 📅 Nov 8 │  │
│  │ 💬 4 comments            →    │  │
│  └───────────────────────────────┘  │
│  ┌───────────────────────────────┐  │
│  │ ⚡ Power Outage               │  │
│  │ [🟢 Solved] [🔴 Urgent]      │  │
│  │ Frequent power cuts...        │  │
│  │ 🏢 Green Valley PG • 📅 Nov 5 │  │
│  │ 💬 6 comments            →    │  │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘
```

**Key Features**:
- 🎴 Card-based layout
- 🏷️ Color-coded status badges
- 🚩 Priority indicators
- 📊 Comment count
- 🎨 Left border shows priority
- 📱 Full-width on mobile
- 🖱️ Tap/click anywhere to open

---

### 2️⃣ Create Complaint Page
**URL**: `/accounts/complaints/create/`

```
┌─────────────────────────────────────┐
│  ← Back                             │
│                                     │
│  ➕ Create New Complaint            │
│  Provide details about your issue   │
├─────────────────────────────────────┤
│  📍 Select Your Booking *           │
│  ┌─────────────────────────────┐   │
│  │ Green Valley - Room 101 (1) ▼│   │
│  └─────────────────────────────┘   │
├─────────────────────────────────────┤
│  ✏️ Complaint Title *               │
│  ┌─────────────────────────────┐   │
│  │ WiFi not working in room... │   │
│  └─────────────────────────────┘   │
├─────────────────────────────────────┤
│  🏷️ Category *                      │
│  ┌────────┬────────┬────────┐      │
│  │[🔧Maint]│[🧹Clean]│[🍽️Food]│      │
│  ├────────┼────────┼────────┤      │
│  │[📡WiFi✓]│[⚡Elect]│[💧Water]│      │
│  ├────────┼────────┼────────┤      │
│  │[🛡️Secur]│[🔊Noise]│[❓Other]│      │
│  └────────┴────────┴────────┘      │
├─────────────────────────────────────┤
│  🚩 Priority Level *                │
│  ○ Low  ● Medium  ○ High  ○ Urgent │
├─────────────────────────────────────┤
│  📝 Detailed Description *          │
│  ┌─────────────────────────────┐   │
│  │ WiFi has been down since    │   │
│  │ yesterday morning. Unable   │   │
│  │ to work from home...        │   │
│  │                             │   │
│  └─────────────────────────────┘   │
├─────────────────────────────────────┤
│  [📤 Submit Complaint] [Cancel]    │
└─────────────────────────────────────┘
```

**Key Features**:
- 📋 Step-by-step form
- 🎨 Visual category selection
- 🔘 Radio buttons for priority
- ✅ Real-time validation
- 📱 Mobile-optimized inputs
- 💾 Auto-save draft (optional)

---

### 3️⃣ Complaint Detail Page
**URL**: `/accounts/complaints/{id}/`

```
┌─────────────────────────────────────┐
│  ← Back to My Complaints            │
│                                     │
│  🔧 WiFi Not Working                │
│  [🔴 Open] [🔴 Urgent]              │
├─────────────────────────────────────┤
│  📊 Details                         │
│  🏢 PG: Green Valley PG             │
│  🚪 Room: 101 - Share 1             │
│  📅 Created: November 10, 2024      │
├─────────────────────────────────────┤
│  📄 Description                     │
│  ┌─────────────────────────────┐   │
│  │ WiFi has been completely    │   │
│  │ down since yesterday. I'm   │   │
│  │ unable to work from home.   │   │
│  │ Please resolve urgently.    │   │
│  └─────────────────────────────┘   │
├─────────────────────────────────────┤
│  💬 Updates & Comments (2)          │
│  ┌─────────────────────────────┐   │
│  │ ● Admin Response             │   │
│  │   Nov 10, 2:30 PM           │   │
│  │   ────────────────────       │   │
│  │   We've contacted the ISP.  │   │
│  │   Technician scheduled for  │   │
│  │   tomorrow morning.         │   │
│  └─────────────────────────────┘   │
│  ┌─────────────────────────────┐   │
│  │ ● Admin Response             │   │
│  │   Nov 11, 9:00 AM           │   │
│  │   ────────────────────       │   │
│  │   WiFi restored and tested. │   │
│  │   Please let us know if OK. │   │
│  └─────────────────────────────┘   │
│                                     │
│  ℹ️ Your complaint is being reviewed│
└─────────────────────────────────────┘
```

**Key Features**:
- 📊 Complete complaint info
- ⏰ Timeline view
- 💬 All admin responses visible
- 🔒 Internal notes hidden
- 📱 Scrollable on mobile
- 🎨 Clean, readable layout

---

## 👨‍💼 ADMIN INTERFACE

### 1️⃣ Complaint Dashboard
**URL**: `/pg/complaints/`

```
┌─────────────────────────────────────────────────────┐
│  🛡️ Complaint Management                            │
│  Monitor and resolve tenant complaints              │
├─────────────────────────────────────────────────────┤
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐              │
│  │  45  │ │  12  │ │   8  │ │  20  │              │
│  │Total │ │ Open │ │In Prog│ │Solved│              │
│  └──────┘ └──────┘ └──────┘ └──────┘              │
├─────────────────────────────────────────────────────┤
│  🔍 Filters & Search                        [↕]    │
│  ┌────┬──────┬────────┬────────┬─────────┐        │
│  │PG ▼│Status▼│Priority▼│Category▼│Search...│       │
│  └────┴──────┴────────┴────────┴─────────┘        │
│  From: [📅] To: [📅] Sort: [Newest ▼] [Apply]     │
├─────────────────────────────────────────────────────┤
│  Complaint            │Tenant│Status│Priority│View │
│  ────────────────────────────────────────────────  │
│  🔧 WiFi Not Working  │John D│[🔴]  │[🔴 Urg]│[→] │
│  Green Valley PG      │R-101 │Open  │        │    │
│  ────────────────────────────────────────────────  │
│  🧹 Room Cleanliness  │Sarah │[🟡]  │[🔵 Med]│[→] │
│  Green Valley PG      │R-205 │InProg│        │    │
│  ────────────────────────────────────────────────  │
│  💧 Water Pressure    │Mike P│[🔴]  │[🟠 High]│[→] │
│  Sunset PG            │R-102 │Open  │        │    │
│  ────────────────────────────────────────────────  │
│  ⚡ Power Backup      │Lisa W│[🟢]  │[🔴 Urg]│[→] │
│  Green Valley PG      │R-101 │Solved│        │    │
└─────────────────────────────────────────────────────┘
```

**Key Features**:
- 📊 Statistics at top
- 🔍 Advanced filters
- 🗂️ Sortable columns
- 🎨 Color-coded rows
- 📱 Responsive table
- 🖱️ Click row to open
- 📉 Auto-refresh (optional)

---

### 2️⃣ Complaint Management Page
**URL**: `/pg/complaints/{id}/`

```
┌─────────────────────────────────────────────────────────┐
│  ← Back to All Complaints                               │
│                                                          │
│  🔧 WiFi Not Working      [🔴 Open] [🔴 Urgent]         │
├──────────────────────────────────┬──────────────────────┤
│  👤 Tenant Details               │ ⚡ Quick Actions     │
│  Name: John Doe                  │                      │
│  Email: john@example.com         │ Status:              │
│  PG: Green Valley PG             │ ┌──────────────┐    │
│  Room: 101 - Share 1             │ │ Open      ▼ │    │
│  Created: Nov 10, 2024           │ └──────────────┘    │
│                                  │                      │
│  📄 Description                  │ Priority:            │
│  ┌────────────────────────────┐ │ ┌──────────────┐    │
│  │ WiFi completely down since │ │ │ Urgent    ▼ │    │
│  │ yesterday. Unable to work  │ │ └──────────────┘    │
│  │ from home. Urgent help     │ │                      │
│  │ needed.                    │ │ ─────────────────    │
│  └────────────────────────────┘ │                      │
│                                  │ 💬 Add Comment       │
│  💬 Timeline (3)                 │ ┌──────────────┐    │
│  ┌────────────────────────────┐ │ │Type response │    │
│  │ ● Admin Response            │ │ │here...       │    │
│  │   Nov 10, 2:30 PM          │ │ │              │    │
│  │   ─────────────────────    │ │ │              │    │
│  │   Contacted ISP. Tech      │ │ └──────────────┘    │
│  │   coming tomorrow morning. │ │ ☐ Internal note     │
│  └────────────────────────────┘ │ [📤 Post Comment]   │
│  ┌────────────────────────────┐ │                      │
│  │ ⚠️ Internal Note            │ │                      │
│  │   Nov 10, 3:00 PM          │ │                      │
│  │   ─────────────────────    │ │                      │
│  │   ISP says line cut by     │ │                      │
│  │   construction work.       │ │                      │
│  └────────────────────────────┘ │                      │
│  ┌────────────────────────────┐ │                      │
│  │ ● Admin Response            │ │                      │
│  │   Nov 11, 9:00 AM          │ │                      │
│  │   ─────────────────────    │ │                      │
│  │   Fixed! WiFi restored.    │ │                      │
│  └────────────────────────────┘ │                      │
└──────────────────────────────────┴──────────────────────┘
```

**Key Features**:
- 📋 Split layout (desktop)
- 🎯 Quick actions sidebar
- ⚡ Instant updates (AJAX)
- 💬 Comment timeline
- 🔒 Internal notes (yellow)
- 📱 Stacked on mobile
- 📌 Sticky sidebar
- ✅ Real-time feedback

---

## 🎨 Design System

### Color Palette

```
Primary:   #0d6efd (Blue)   - Actions, links
Success:   #198754 (Green)  - Solved status
Danger:    #dc3545 (Red)    - Open, urgent
Warning:   #ffc107 (Yellow) - In progress
Secondary: #6c757d (Gray)   - Not solved, low priority
Info:      #0dcaf0 (Cyan)   - Information
```

### Typography

```
Headings:  System fonts, Semi-bold
Body:      System fonts, Regular
Monospace: SF Mono, Consolas, Monaco
Sizes:     Responsive (clamp functions)
```

### Spacing

```
xs:  0.25rem (4px)
sm:  0.5rem  (8px)
md:  1rem    (16px)
lg:  1.5rem  (24px)
xl:  2rem    (32px)
```

### Shadows

```
xs: 0 1px 2px rgba(0,0,0,.04)
sm: 0 2px 4px rgba(0,0,0,.08)
md: 0 4px 10px rgba(0,0,0,.12)
lg: 0 8px 20px rgba(0,0,0,.16)
```

### Border Radius

```
sm: 0.45rem
md: 0.85rem
lg: 1.25rem
pill: 50rem (fully rounded)
```

---

## 📱 Responsive Breakpoints

```
< 576px   Mobile (Stack everything)
576-768   Large Mobile (2-column grids)
768-992   Tablet (Sidebar visible)
992-1200  Desktop (Full layout)
> 1200    Large Desktop (Max width)
```

### Mobile Adaptations

```
✅ Filters collapse
✅ Tables show essential columns only
✅ Forms full-width
✅ Buttons full-width
✅ Sidebar moves below content
✅ Font sizes scale down
✅ Touch targets 44px minimum
✅ Horizontal scroll on tables
```

---

## 🎭 Interactive Elements

### Hover Effects

```css
Cards:    Shadow increases, slight lift
Buttons:  Background darkens
Links:    Color changes, underline
Badges:   Scale up slightly
Rows:     Background color change
```

### Loading States

```
Buttons:    [⏳ Submitting...]
AJAX:       Spinner overlay
Forms:      Disabled + opacity
Updates:    Fade transition
```

### Empty States

```
No complaints: 📭 Inbox icon + message
No comments:   💬 Chat icon + message
No results:    🔍 Search icon + message
```

---

## ✨ Animation & Transitions

```css
All transitions: 0.3s ease
Fade in:        0.4s
Scale:          0.2s
Slide:          0.5s
```

---

## 🔔 User Feedback

### Success Messages
```
✅ Green toast at top
✅ Auto-dismiss after 3s
✅ Slide in from top
```

### Error Messages
```
❌ Red alert box
❌ Stays until dismissed
❌ Shake animation
```

### Info Messages
```
ℹ️ Blue info box
ℹ️ Stays until dismissed
ℹ️ Fade in
```

---

## 📊 Status & Priority Visual Guide

### Status Badges

```
🔴 OPEN         - Red, urgent attention
🟡 IN PROGRESS  - Yellow, being worked on
🟢 SOLVED       - Green, resolved successfully
⚪ NOT SOLVED   - Gray, couldn't resolve
⚫ CLOSED       - Dark, archived
```

### Priority Badges

```
🔴 URGENT  - Red background, white text
🟠 HIGH    - Orange background, dark text
🔵 MEDIUM  - Blue background, white text
⚫ LOW     - Gray background, white text
```

### Category Icons

```
🔧 Maintenance     - bi-tools
🧹 Cleanliness     - bi-spray
🍽️ Food            - bi-egg-fried
📡 WiFi            - bi-wifi
⚡ Electricity     - bi-lightning-charge
💧 Water           - bi-droplet
🛡️ Security        - bi-shield-check
🔊 Noise           - bi-volume-up
❓ Other           - bi-question-circle
```

---

## 🎯 Best Practices Implemented

### Accessibility
```
✅ Semantic HTML
✅ ARIA labels
✅ Keyboard navigation
✅ Focus indicators
✅ Color contrast (WCAG AA)
✅ Screen reader friendly
```

### Performance
```
✅ Minimal JavaScript
✅ CSS animations (GPU)
✅ Image optimization
✅ Lazy loading ready
✅ No layout shift
```

### UX
```
✅ Clear CTAs
✅ Consistent patterns
✅ Error prevention
✅ Helpful hints
✅ Loading indicators
✅ Success confirmation
```

---

## 🎉 Summary

The UI is:
- 🎨 **Beautiful** - Modern, clean design
- 📱 **Responsive** - Perfect on all devices
- ⚡ **Fast** - Optimized performance
- 🎯 **Intuitive** - Easy to use
- ♿ **Accessible** - WCAG compliant
- 🔒 **Secure** - Proper validations

**Ready for Production!** 🚀
