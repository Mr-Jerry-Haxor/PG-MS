from django.contrib.auth.decorators import login_required
from django.utils.dateparse import parse_date

@login_required
def booking_joining_update(request, booking_id):
    from bookings.models import Booking
    booking = get_object_or_404(Booking, pk=booking_id)
    # Authorization: must be a PG Admin and admin of the booking's PG
    u = request.user
    if not _require_pg_admin(u) or not _admin_pgs(u).filter(id=getattr(booking, 'pg_id', None)).exists():
        messages.error(request, 'PG Admin access required for this PG.')
        return redirect('pg_resident_applications')
    if request.method != 'POST':
        messages.error(request, 'Unsupported request method.')
        return redirect('pg_resident_applications')
    date_str = (request.POST.get('joining_date') or '').strip()
    if not date_str:
        messages.error(request, 'Joining date is required.')
        return redirect(request.META.get('HTTP_REFERER') or 'pg_resident_applications')
    dt = parse_date(date_str)
    if not dt:
        messages.error(request, 'Invalid date format. Use YYYY-MM-DD.')
        return redirect(request.META.get('HTTP_REFERER') or 'pg_resident_applications')
    try:
        booking.joining_date = dt
        booking.save(update_fields=['joining_date'])
        messages.success(request, f'Joining date updated to {dt}.')
    except Exception as e:
        messages.error(request, f'Could not update joining date: {e}')
    return redirect(request.META.get('HTTP_REFERER') or 'pg_resident_applications')
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.core.mail import send_mail
from django.utils.dateparse import parse_date
from django.db import IntegrityError
from django.db.models import Count, Q, Min, Prefetch
try:
    from allauth.account.models import EmailAddress
except Exception:  # allauth not strictly required at import time
    EmailAddress = None

from .models import PG, PGAdmin
from bookings.models import Room, RoomShareStatus, Booking
from bookings.models import ResidentApplication
from .forms import PGForm, RoomForm, ShareStatusForm
from core.models import Notification
from core.audit import log
from django.db.models import Exists, OuterRef
from django.http import HttpResponse


def _require_pg_admin(user):
    # Superusers and website admins can access PG-admin area
    if getattr(user, 'is_superuser', False):
        return True
    if hasattr(user, 'profile') and getattr(user.profile, 'is_website_admin', False):
        return True
    # Fall back to explicit PGAdmin membership (do not rely solely on profile flags)
    try:
        if PGAdmin.objects.filter(user=user).exists():
            return True
    except Exception:
        pass
    return hasattr(user, 'profile') and getattr(user.profile, 'is_pg_admin', False) and getattr(user.profile, 'status', 'active') == 'active'


def _admin_pgs(user):
    # Superusers and website admins see all PGs
    if getattr(user, 'is_superuser', False) or (hasattr(user, 'profile') and getattr(user.profile, 'is_website_admin', False)):
        return PG.objects.all().order_by('name')
    return PG.objects.filter(admins__user=user).order_by('name')


def _active_pg(request):
    """Resolve the active PG for a PG Admin user via ?pg=, session, or first available."""
    qs = _admin_pgs(request.user)
    pg = None
    pg_id = request.GET.get('pg') or request.session.get('active_pg_id')
    if pg_id:
        # Only allow selecting PGs within authorized set
        pg = qs.filter(id=pg_id).first()
    if not pg:
        pg = qs.first()
    if pg:
        request.session['active_pg_id'] = pg.id
    return pg


@login_required
def my_pg(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    # Allow switching if admin of multiple PGs
    pg = _active_pg(request) or PG.objects.filter(created_by_admin=request.user).first()
    # Auto-convert past-dated VACANT_FROM shares to VACANT on dashboard load
    if pg:
        try:
            today = timezone.now().date()
            qs_cleanup = RoomShareStatus.objects.filter(
                room__pg=pg,
                status=RoomShareStatus.VACANT_FROM,
                vacant_from__isnull=False,
                vacant_from__lt=today,
            )
            if qs_cleanup.exists():
                qs_cleanup.update(status=RoomShareStatus.VACANT, vacant_from=None)
        except Exception:
            # Non-blocking: ignore failures in cleanup
            pass
    if request.method == 'POST':
        form = PGForm(request.POST, instance=pg)
        if form.is_valid():
            pg = form.save(commit=False)
            if not pg.pk:
                pg.created_by_admin = request.user
            pg.save()
            messages.success(request, "PG saved.")
            return redirect('pg_my')
    else:
        form = PGForm(instance=pg)
    return render(request, 'pgadmin/my_pg.html', {"form": form, "pg": pg, "pgs": list(_admin_pgs(request.user))})


@login_required
def rooms_list(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    pg = _active_pg(request)
    if pg:
        rooms = (
            Room.objects.filter(pg=pg)
            .annotate(
                occupied_count=Count('shares', filter=Q(shares__status=RoomShareStatus.OCCUPIED)),
                reserved_count=Count('shares', filter=Q(shares__status=RoomShareStatus.RESERVED)),
                vacant_count=Count('shares', filter=Q(shares__status=RoomShareStatus.VACANT)),
                leaving_count=Count('shares', filter=Q(shares__status=RoomShareStatus.VACANT_FROM)),
                next_vacant_from=Min('shares__vacant_from', filter=Q(shares__status=RoomShareStatus.VACANT_FROM)),
            )
            .prefetch_related(
                Prefetch(
                    'shares',
                    queryset=RoomShareStatus.objects.filter(status=RoomShareStatus.VACANT_FROM).only('id','share_no','vacant_from').order_by('vacant_from'),
                    to_attr='vacant_from_shares',
                )
            )
            .order_by('room_no')
        )
        # Apply optional filter by room share status
        only = (request.GET.get('filter') or '').strip().lower()
        if only == 'vacant':
            rooms = rooms.filter(vacant_count__gt=0)
        elif only == 'leaving':
            rooms = rooms.filter(leaving_count__gt=0)
        elif only == 'reserved':
            rooms = rooms.filter(reserved_count__gt=0)
        elif only == 'occupied':
            rooms = rooms.filter(occupied_count__gt=0)
    else:
        rooms = []
    return render(request, 'pgadmin/rooms_list.html', {"pg": pg, "rooms": rooms, "pgs": list(_admin_pgs(request.user)), "active_filter": (request.GET.get('filter') or '')})


@login_required
@transaction.atomic
def room_create(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    pg = _active_pg(request)
    if request.method == 'POST':
        form = RoomForm(request.POST)
        if form.is_valid():
            room = form.save(commit=False)
            room.pg = pg
            room.save()
            # Ensure share rows exist
            for i in range(1, room.total_shares + 1):
                RoomShareStatus.objects.get_or_create(room=room, share_no=i)
            messages.success(request, "Room created.")
            return redirect('pg_rooms')
    else:
        form = RoomForm()
    return render(request, 'pgadmin/room_form.html', {"form": form, "pg": pg, "pgs": list(_admin_pgs(request.user))})


@login_required
@transaction.atomic
def room_edit(request, pk):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    room = get_object_or_404(Room, pk=pk)
    if not _admin_pgs(request.user).filter(id=room.pg_id).exists():
        messages.error(request, "PG Admin access required for this PG.")
        return redirect('dashboard')
    if request.method == 'POST':
        form = RoomForm(request.POST, instance=room)
        if form.is_valid():
            room = form.save()
            # Sync shares count by adding missing ones (no deletion for safety)
            existing = room.shares.count()
            for i in range(existing + 1, room.total_shares + 1):
                RoomShareStatus.objects.get_or_create(room=room, share_no=i)
            messages.success(request, "Room updated.")
            return redirect('pg_rooms')
    else:
        form = RoomForm(instance=room)
    return render(request, 'pgadmin/room_form.html', {"form": form, "room": room})


@login_required
def application_pdf(request, app_id):
    """Generate a structured PDF of a resident application for PG Admins.
    Includes selfie (if available) and appends Aadhaar PDF pages when possible.
    """
    app = get_object_or_404(ResidentApplication, pk=app_id)
    # Authorization: must be PG Admin for this PG
    user = request.user
    pg_id = getattr(app, 'pg_id', None)
    if not _require_pg_admin(user) or not _admin_pgs(user).filter(id=pg_id).exists():
        messages.error(request, 'PG Admin access required for this PG.')
        return redirect('pg_resident_applications')

    # Lazy imports to avoid hard dependency at import-time
    try:
        from io import BytesIO
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
        from reportlab.lib import colors
        import requests, re
        from urllib.parse import urlparse, parse_qs
    except Exception as e:
        return HttpResponse(f"PDF generation dependencies missing: {e}", status=500)

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    story = []

    title = f"Resident Application — {app.name or app.user.first_name} {app.user.last_name}"
    story.append(Paragraph(title, styles['Title']))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"PG: {app.pg.name}", styles['Normal']))
    story.append(Paragraph(f"Room: {getattr(app.room, 'room_no', '—')} • Share: {getattr(getattr(app, 'booking', None), 'share_no', '—')}", styles['Normal']))
    story.append(Spacer(1, 12))

    # Helpers for downloading
    def _normalize_direct_url(url: str) -> str:
        if not url:
            return url
        try:
            pu = urlparse(url)
            host = pu.netloc.lower()
            # Google Drive: file/d/<id>/view or open?id=<id>
            if 'drive.google.com' in host:
                m = re.search(r"/file/d/([\w-]+)/", pu.path)
                file_id = None
                if m:
                    file_id = m.group(1)
                else:
                    qs = parse_qs(pu.query)
                    file_id = (qs.get('id') or qs.get('file_id') or [None])[0]
                if file_id:
                    return f"https://drive.google.com/uc?export=download&id={file_id}"
            # Dropbox: ensure dl=1
            if 'dropbox.com' in host and 'dl=0' in url:
                return url.replace('dl=0', 'dl=1')
        except Exception:
            return url
        return url

    def _download(url: str, accept: str) -> bytes | None:
        if not url:
            return None
        url2 = _normalize_direct_url(url)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': accept,
            'Referer': '',
        }
        try:
            r = requests.get(url2, headers=headers, timeout=20, allow_redirects=True)
            if r.ok and r.content:
                return r.content
        except Exception:
            return None
        return None

    def _download_with_type(url: str, accept: str):
        if not url:
            return None, None
        url2 = _normalize_direct_url(url)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': accept,
            'Referer': '',
        }
        try:
            r = requests.get(url2, headers=headers, timeout=20, allow_redirects=True)
            if r.ok and r.content:
                return r.content, r.headers.get('content-type', '').lower()
        except Exception:
            return None, None
        return None, None

    # Selfie
    if getattr(app, 'selfie_url', None):
        selfie_bytes = _download(app.selfie_url, 'image/*')
        if selfie_bytes:
            try:
                img = RLImage(BytesIO(selfie_bytes))
                img._restrictSize(180, 180)
                story.append(Paragraph("Selfie:", styles['Heading4']))
                story.append(img)
                story.append(Spacer(1, 8))
            except Exception:
                story.append(Paragraph("Selfie: [Unable to load image binary]", styles['Normal']))
        else:
            # Fallback: include link when download not possible
            story.append(Paragraph(f"Selfie: <link href='{app.selfie_url}'>Open online</link>", styles['Normal']))

    # Info table
    info = [
        ["Name", f"{app.name or app.user.first_name} {app.user.last_name}"],
        ["DOB", f"{app.dob or '—'}"],
        ["Age", f"{app.age or '—'}"],
        ["Phone", f"{app.phone or getattr(getattr(app.user, 'profile', None), 'phone', '—')}"],
        ["Email", f"{app.email or app.user.email}"],
        ["Food Pref", f"{app.food_pref or '—'}"],
        ["Marital", f"{app.marital_status or '—'}"],
        ["Date of Admission", f"{app.date_of_admission or '—'}"],
        ["Address", f"{app.address or '—'}"],
    ]
    table = Table(info, hAlign='LEFT', colWidths=[120, 360])
    table.setStyle(TableStyle([
        ('FONT', (0,0), (-1,-1), 'Helvetica', 9),
        ('BACKGROUND', (0,0), (-1,0), colors.whitesmoke),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.whitesmoke]),
        ('GRID', (0,0), (-1,-1), 0.25, colors.lightgrey),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('TOPPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(table)
    story.append(Spacer(1, 10))

    # Family
    fam = [
        ["Father Name", f"{app.father_name or '—'}"],
        ["Father Phone", f"{app.father_phone or '—'}"],
        ["Mother Name", f"{app.mother_name or '—'}"],
        ["Mother Phone", f"{app.mother_phone or '—'}"],
    ]
    story.append(Paragraph("Family", styles['Heading4']))
    story.append(Table(fam, colWidths=[120, 360], style=[('FONT', (0,0), (-1,-1), 'Helvetica', 9), ('GRID', (0,0), (-1,-1), 0.25, colors.lightgrey), ('ROWBACKGROUNDS', (0,0), (-1,-1), [colors.white, colors.whitesmoke])]))
    story.append(Spacer(1, 10))

    # Education / Work
    work = [
        ["Occupation", f"{app.occupation or '—'}"],
        ["Education", f"{app.education or '—'}"],
        ["Organisation", f"{app.org_name or '—'}"],
        ["Org Address", f"{app.org_address or '—'}"],
    ]
    story.append(Paragraph("Education / Work", styles['Heading4']))
    story.append(Table(work, colWidths=[120, 360], style=[('FONT', (0,0), (-1,-1), 'Helvetica', 9), ('GRID', (0,0), (-1,-1), 0.25, colors.lightgrey), ('ROWBACKGROUNDS', (0,0), (-1,-1), [colors.white, colors.whitesmoke])]))
    story.append(Spacer(1, 10))

    # Vehicle
    vehicle_rows = [["Has Vehicle", "Yes" if app.has_vehicle else "No"]]
    if app.has_vehicle:
        vehicle_rows.append(["Number", f"{app.vehicle_number or '—'}"])
        vehicle_rows.append(["Model", f"{app.vehicle_model or '—'}"])
    story.append(Paragraph("Vehicle", styles['Heading4']))
    story.append(Table(vehicle_rows, colWidths=[120, 360], style=[('FONT', (0,0), (-1,-1), 'Helvetica', 9), ('GRID', (0,0), (-1,-1), 0.25, colors.lightgrey), ('ROWBACKGROUNDS', (0,0), (-1,-1), [colors.white, colors.whitesmoke])]))
    story.append(Spacer(1, 10))

    # Aadhaar summary and prefetch document before building main PDF
    story.append(Paragraph("Documents", styles['Heading4']))
    story.append(Paragraph(f"Aadhaar Number: {app.aadhaar_number or '—'}", styles['Normal']))

    aadhaar_pdf_bytes = None
    if getattr(app, 'aadhaar_file_url', None):
        content, ctype = _download_with_type(app.aadhaar_file_url, 'application/pdf, image/*')
        if content:
            # Heuristics to detect PDF vs Image
            is_pdf = content[:4] == b'%PDF' or (ctype and 'pdf' in ctype)
            if is_pdf:
                aadhaar_pdf_bytes = content
                story.append(Spacer(1, 6))
                story.append(Paragraph("Aadhaar: (attached PDF will be appended)", styles['Italic']))
            else:
                try:
                    story.append(Spacer(1, 6))
                    story.append(Paragraph("Aadhaar Image:", styles['Heading4']))
                    aimg = RLImage(BytesIO(content))
                    aimg._restrictSize(420, 560)  # fit nicely on A4
                    story.append(aimg)
                except Exception:
                    story.append(Paragraph(f"Aadhaar: <link href='{app.aadhaar_file_url}'>Open online</link>", styles['Normal']))
        else:
            story.append(Paragraph(f"Aadhaar: <link href='{app.aadhaar_file_url}'>Open online</link>", styles['Normal']))

    # Declarations
    decls = [
        ["Valuables", "Agreed" if app.decl_valuables else "Not agreed"],
        ["Notice", "Agreed" if app.decl_notice else "Not agreed"],
        ["Deposit", "Agreed" if app.decl_deposit else "Not agreed"],
        ["Truth", "Agreed" if app.decl_truth else "Not agreed"],
    ]
    story.append(Spacer(1, 6))
    story.append(Paragraph("Declarations", styles['Heading4']))
    story.append(Table(decls, colWidths=[120, 360], style=[('FONT', (0,0), (-1,-1), 'Helvetica', 9), ('GRID', (0,0), (-1,-1), 0.25, colors.lightgrey), ('ROWBACKGROUNDS', (0,0), (-1,-1), [colors.white, colors.whitesmoke])]))

    # Build main doc
    doc.build(story)
    main_pdf = buf.getvalue()

    # Attempt to merge PDFs if possible
    final_bytes = main_pdf
    if aadhaar_pdf_bytes:
        try:
            from pypdf import PdfReader, PdfWriter
            main_reader = PdfReader(BytesIO(main_pdf))
            out = PdfWriter()
            for p in main_reader.pages:
                out.add_page(p)
            aadhaar_reader = PdfReader(BytesIO(aadhaar_pdf_bytes))
            for p in aadhaar_reader.pages:
                out.add_page(p)
            out_buf = BytesIO()
            out.write(out_buf)
            final_bytes = out_buf.getvalue()
        except Exception:
            # Fallback: keep main PDF only if merge fails
            final_bytes = main_pdf

    filename = f"Resident-Application-{(app.name or app.user.first_name or 'User').replace(' ', '-')}-{app.id}.pdf"
    resp = HttpResponse(final_bytes, content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


@login_required
def room_shares(request, pk):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    room = get_object_or_404(Room, pk=pk)
    if not _admin_pgs(request.user).filter(id=room.pg_id).exists():
        messages.error(request, "PG Admin access required for this PG.")
        return redirect('dashboard')
    shares = room.shares.order_by('share_no')
    forms = [ShareStatusForm(prefix=f"s{rs.id}", instance=rs) for rs in shares]
    if request.method == 'POST':
        any_saved = False
        for rs in shares:
            prev_status = rs.status
            form = ShareStatusForm(request.POST, prefix=f"s{rs.id}", instance=rs)
            if form.is_valid():
                new_status = form.cleaned_data.get('status')
                # If moving from vacant/vacant_from to reserved/occupied, collect user details and create or link booking
                if prev_status in [RoomShareStatus.VACANT, RoomShareStatus.VACANT_FROM] and new_status in [RoomShareStatus.RESERVED, RoomShareStatus.OCCUPIED]:
                    email = request.POST.get(f"s{rs.id}-new-email", "").strip()
                    first_name = request.POST.get(f"s{rs.id}-new-first_name", "").strip()
                    last_name = request.POST.get(f"s{rs.id}-new-last_name", "").strip()
                    phone = request.POST.get(f"s{rs.id}-new-phone", "").strip()
                    joining_raw = request.POST.get(f"s{rs.id}-new-joining", "").strip()
                    joining_date = parse_date(joining_raw) if joining_raw else None

                    if not email:
                        messages.error(request, f"Share {rs.share_no}: Email is required to set {new_status}.")
                        continue

                    User = get_user_model()
                    user = User.objects.filter(email__iexact=email).first()
                    if not user:
                        # Create a lightweight account; user can later sign in via Google with same email
                        # Ensure unique username
                        base_username = email.split('@')[0][:20] or 'user'
                        username = base_username
                        suffix = 1
                        while User.objects.filter(username=username).exists():
                            suffix += 1
                            username = f"{base_username}{suffix}"
                        user = User(username=username, email=email, first_name=first_name or '', last_name=last_name or '')
                        user.set_unusable_password()
                        user.save()
                        created_user = True
                    else:
                        created_user = False
                        updated = False
                        if first_name and user.first_name != first_name:
                            user.first_name = first_name
                            updated = True
                        if last_name and user.last_name != last_name:
                            user.last_name = last_name
                            updated = True
                        if updated:
                            user.save(update_fields=['first_name', 'last_name'])

                    # Ensure an EmailAddress record exists for allauth and mark verified for seamless linking on future login
                    if EmailAddress is not None:
                        ea, _ = EmailAddress.objects.get_or_create(user=user, email=user.email, defaults={'verified': True, 'primary': True})
                        # If not primary/verified, make it so
                        to_update = []
                        if not ea.primary:
                            ea.primary = True
                            to_update.append('primary')
                        if not ea.verified:
                            ea.verified = True
                            to_update.append('verified')
                        if to_update:
                            ea.save(update_fields=to_update)

                    # Ensure profile exists and update phone
                    profile = getattr(user, 'profile', None)
                    if profile:
                        if phone and profile.phone != phone:
                            profile.phone = phone
                            profile.save(update_fields=['phone'])
                    any_saved = True

                    # Create the booking according to status
                    try:
                        booking_status = Booking.APPROVED if new_status == RoomShareStatus.OCCUPIED else Booking.PENDING
                        booking = Booking(
                            user=user,
                            room=room,
                            share_no=rs.share_no,
                            status=booking_status,
                            joining_date=joining_date,
                        )
                        if booking_status == Booking.APPROVED:
                            booking.start_date = joining_date or timezone.now().date()
                        booking.save()
                    except IntegrityError:
                        messages.error(request, f"Share {rs.share_no}: Could not create booking. User may already have an active booking in this PG.")
                        # Skip saving status change for this share
                        continue

                    # Save the share status change after booking created and clear vacant_from if set
                    saved_rs = form.save()
                    if getattr(saved_rs, 'vacant_from', None):
                        saved_rs.vacant_from = None
                        saved_rs.save(update_fields=['vacant_from'])
                    # Feedback
                    if created_user:
                        messages.success(request, f"Share {rs.share_no}: User created: {user.email}, booking: {booking.get_status_display()}.")
                    else:
                        messages.success(request, f"Share {rs.share_no}: User linked: {user.email}, booking: {booking.get_status_display()}.")
                else:
                    # Normal save and inline occupant updates when already occupied
                    saved_rs = form.save()
                    # If status changed away from VACANT_FROM, clear the date
                    if prev_status == RoomShareStatus.VACANT_FROM and saved_rs.status != RoomShareStatus.VACANT_FROM and getattr(saved_rs, 'vacant_from', None):
                        saved_rs.vacant_from = None
                        saved_rs.save(update_fields=['vacant_from'])
                    any_saved = True
                    occ = (
                        Booking.objects.filter(room=room, share_no=rs.share_no, status=Booking.APPROVED)
                        .select_related('user', 'user__profile')
                        .order_by('-created_at')
                        .first()
                    )
                    if occ:
                        phone_key = f"s{rs.id}-phone"
                        leaving_key = f"s{rs.id}-leaving"
                        phone_val = request.POST.get(phone_key)
                        leaving_val = request.POST.get(leaving_key)
                        if phone_val is not None and hasattr(occ.user, 'profile'):
                            if occ.user.profile.phone != phone_val:
                                occ.user.profile.phone = phone_val
                                occ.user.profile.save(update_fields=['phone'])
                        if leaving_val is not None:
                            new_date = parse_date(leaving_val) if leaving_val else None
                            if occ.leaving_date != new_date:
                                occ.leaving_date = new_date
                                occ.save(update_fields=['leaving_date'])
        if any_saved:
            messages.success(request, "Changes saved.")
            return redirect('pg_room_shares', pk=room.id)
    # Build (share, form, occupant) where occupant is latest approved booking for this share
    rs_forms = []
    for rs, form in zip(shares, forms):
        occupant = None
        if rs.status == RoomShareStatus.OCCUPIED:
            occupant = (
                Booking.objects.filter(room=room, share_no=rs.share_no, status=Booking.APPROVED)
                .select_related('user', 'room', 'user__profile')
                .order_by('-created_at')
                .first()
            )
        # Determine application status for occupant booking
        app_exists = False
        if occupant:
            from bookings.models import ResidentApplication
            app_exists = ResidentApplication.objects.filter(booking=occupant).exists()
        rs_forms.append((rs, form, occupant, app_exists))
    return render(request, 'pgadmin/room_shares.html', {"room": room, "forms": forms, "shares": shares, "rs_forms": rs_forms})


# --- Booking approvals (PG Admin) ---

@login_required
def bookings_pending(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    pg = _active_pg(request)
    pending = []
    if pg:
        pending = (
            Booking.objects.filter(status=Booking.PENDING, room__pg=pg)
            .select_related('user', 'room')
            .annotate(has_application=Exists(ResidentApplication.objects.filter(booking_id=OuterRef('pk'))))
        )
    return render(request, 'pgadmin/bookings_pending.html', {"pg": pg, "bookings": pending, "pgs": list(_admin_pgs(request.user))})


@login_required
@transaction.atomic
def booking_approve(request, booking_id):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    booking = get_object_or_404(Booking, pk=booking_id, status=Booking.PENDING)
    # Enforce PG membership
    if not _admin_pgs(request.user).filter(id=(getattr(booking, 'pg_id', None) or getattr(getattr(booking, 'room', None), 'pg_id', None))).exists():
        messages.error(request, "PG Admin access required for this PG.")
        return redirect('dashboard')
    share = get_object_or_404(RoomShareStatus, room=booking.room, share_no=booking.share_no)
    booking.status = Booking.APPROVED
    booking.start_date = timezone.now().date()
    booking.save()
    share.status = RoomShareStatus.OCCUPIED
    share.save(update_fields=['status'])
    log(request.user, 'booking_approved', 'Booking', booking.id, f"Approved for room {booking.room.room_no} share {booking.share_no}")
    # Notify user
    Notification.objects.create(user=booking.user, title="Booking approved", message=f"Your booking for {booking.room} share {booking.share_no} was approved.")
    try:
        from django.urls import reverse
        link = request.build_absolute_uri(reverse('application_fill', args=[booking.id]))
        send_mail(
            subject="PG-MS: Booking Approved",
            message=f"Your booking for {booking.room} share {booking.share_no} was approved.\nPlease complete your resident application here: {link}",
            from_email=None,
            recipient_list=[booking.user.email],
            fail_silently=True,
        )
    except Exception:
        pass
    messages.success(request, "Booking approved and user notified.")
    return redirect('pg_bookings_pending')


@login_required
@transaction.atomic
def booking_reject(request, booking_id):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    booking = get_object_or_404(Booking, pk=booking_id, status=Booking.PENDING)
    if not _admin_pgs(request.user).filter(id=(getattr(booking, 'pg_id', None) or getattr(getattr(booking, 'room', None), 'pg_id', None))).exists():
        messages.error(request, "PG Admin access required for this PG.")
        return redirect('dashboard')
    share = get_object_or_404(RoomShareStatus, room=booking.room, share_no=booking.share_no)
    booking.status = Booking.REJECTED
    booking.save(update_fields=['status'])
    # On rejection, revert share to appropriate availability state.
    # Rule: if share.vacant_from is in the future -> keep VACANT_FROM (future scheduled vacancy)
    # else (date is past or today OR no date) -> mark as VACANT now.
    today = timezone.now().date()
    update_fields = ['status']
    if share.vacant_from and share.vacant_from > today:
        share.status = RoomShareStatus.VACANT_FROM
        # keep vacant_from date
    else:
        share.status = RoomShareStatus.VACANT
        if share.vacant_from:
            # Clear stale date once it's already vacant
            share.vacant_from = None
            update_fields.append('vacant_from')
    share.save(update_fields=update_fields)
    log(request.user, 'booking_rejected', 'Booking', booking.id, f"Rejected for room {booking.room.room_no} share {booking.share_no}")
    Notification.objects.create(user=booking.user, title="Booking rejected", message=f"Your booking for {booking.room} share {booking.share_no} was rejected.")
    try:
        send_mail(
            subject="PG-MS: Booking Rejected",
            message=f"Your booking for {booking.room} share {booking.share_no} was rejected.",
            from_email=None,
            recipient_list=[booking.user.email],
            fail_silently=True,
        )
    except Exception:
        pass
    messages.info(request, "Booking rejected.")
    return redirect('pg_bookings_pending')


@login_required
def leaving_requests(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    pg = _active_pg(request)
    requests_qs = Booking.objects.filter(room__pg=pg, leaving_date__isnull=False, status=Booking.APPROVED).select_related('user', 'room') if pg else []
    today = timezone.now().date() if pg else None
    return render(request, 'pgadmin/leaving_requests.html', {"pg": pg, "bookings": requests_qs, "pgs": list(_admin_pgs(request.user)), "today": today})


@login_required
def application_email_send(request, booking_id):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    booking = get_object_or_404(Booking, pk=booking_id)
    if not _admin_pgs(request.user).filter(id=(getattr(booking, 'pg_id', None) or getattr(getattr(booking, 'room', None), 'pg_id', None))).exists():
        messages.error(request, "PG Admin access required for this PG.")
        return redirect('dashboard')
    try:
        from django.urls import reverse
        link = request.build_absolute_uri(reverse('application_fill', args=[booking.id]))
        sent = send_mail(
            subject="PG-MS: Complete Your Resident Application",
            message=f"Please complete your resident application here: {link}",
            from_email=None,
            recipient_list=[booking.user.email],
            fail_silently=False,
        )
        if sent:
            messages.success(request, f"Application link sent to {booking.user.email}.")
        else:
            messages.error(request, "Email was not sent. Please verify email settings.")
    except Exception as e:
        messages.error(request, f"Could not send email: {e}")
    # Redirect back to applications list if invoked from there
    if request.GET.get('from') == 'applications':
        return redirect('pg_resident_applications')
    return redirect('pg_bookings_pending')


@login_required
@transaction.atomic
def leaving_confirm(request, booking_id):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    booking = get_object_or_404(Booking, pk=booking_id, status=Booking.APPROVED)
    if not _admin_pgs(request.user).filter(id=(getattr(booking, 'pg_id', None) or getattr(getattr(booking, 'room', None), 'pg_id', None))).exists():
        messages.error(request, "PG Admin access required for this PG.")
        return redirect('dashboard')
    share = get_object_or_404(RoomShareStatus, room=booking.room, share_no=booking.share_no)
    # Mark leaving as confirmed (store timestamp/date)
    today = timezone.now().date()
    updated_fields = []
    if not booking.leaving_confirmed_date:
        booking.leaving_confirmed_date = today
        updated_fields.append('leaving_confirmed_date')
    # If leaving date already reached or past, free immediately; else keep occupied until date
    if booking.leaving_date:
        share.status = RoomShareStatus.VACANT_FROM
        share.vacant_from = booking.leaving_date
        share.save(update_fields=['status','vacant_from'])
    if updated_fields:
        booking.save(update_fields=updated_fields)
    # Do not deactivate user profile; adjust flags if needed
    try:
        if hasattr(booking.user, 'profile'):
            if getattr(booking.user.profile, 'is_pg_user', True):
                booking.user.profile.is_pg_user = False
                booking.user.profile.save(update_fields=['is_pg_user'])
    except Exception:
        pass
    log(request.user, 'leaving_confirmed', 'Booking', booking.id, f"Leaving confirmed; booking closed for room {booking.room.room_no} share {booking.share_no}")
    if share.status == RoomShareStatus.VACANT:
        messages.success(request, f"Leaving confirmed and share freed for room {booking.room.room_no}.")
    else:
        messages.success(request, f"Leaving confirmed for room {booking.room.room_no}. Share will free on {booking.leaving_date}.")
    return redirect('pg_leaving_requests')
from django.shortcuts import render

# Create your views here.


@login_required
def resident_applications(request):
    # Show all approved bookings for this PG with application status/actions
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    pg = _active_pg(request)
    bookings = []
    if pg:
        today = timezone.now().date()
        bookings = (
            Booking.objects
            .filter(
                room__pg=pg,
                status=Booking.APPROVED,
            )
            .filter(Q(leaving_date__isnull=True) | Q(leaving_date__gt=today))
            .select_related('user', 'room', 'application')
            .order_by('room__room_no', 'share_no')
        )
    submitted_count = bookings.filter(application__isnull=False).count() if bookings else 0
    pending_count = bookings.filter(application__isnull=True).count() if bookings else 0
    return render(
        request,
        'pgadmin/resident_applications.html',
        {
            "pg": pg,
            "bookings": bookings,
            "pgs": list(_admin_pgs(request.user)),
            "submitted_count": submitted_count,
            "pending_count": pending_count,
        },
    )


@login_required
@transaction.atomic
def application_confirm(request, app_id):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    app = get_object_or_404(ResidentApplication, pk=app_id)
    if not _admin_pgs(request.user).filter(id=getattr(app, 'pg_id', None)).exists():
        messages.error(request, "PG Admin access required for this PG.")
        return redirect('dashboard')
    # Set status to confirmed
    app.status = ResidentApplication.CONFIRMED
    app.save(update_fields=['status'])
    # History
    from bookings.models import ApplicationStatusHistory
    ApplicationStatusHistory.objects.create(application=app, status=app.status, comment='Confirmed by PG admin', by_user=request.user)
    # Notify user
    try:
        from django.urls import reverse
        link = request.build_absolute_uri(reverse('my_application'))
        send_mail(
            subject="PG-MS: Application Confirmed",
            message=f"Your resident application has been confirmed. You can view it here: {link}\nFurther edits are disabled unless requested by admin.",
            from_email=None,
            recipient_list=[app.user.email],
            fail_silently=True,
        )
    except Exception:
        pass
    messages.success(request, "Application confirmed.")
    return redirect('pg_resident_applications')


@login_required
@transaction.atomic
def application_reject(request, app_id):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    app = get_object_or_404(ResidentApplication, pk=app_id)
    if not _admin_pgs(request.user).filter(id=getattr(app, 'pg_id', None)).exists():
        messages.error(request, "PG Admin access required for this PG.")
        return redirect('dashboard')
    reason = (request.POST.get('reason') or '').strip()
    if request.method != 'POST' or not reason:
        messages.error(request, "Rejection reason is required.")
        return redirect('pg_resident_applications')
    app.status = ResidentApplication.REJECTED
    app.save(update_fields=['status'])
    from bookings.models import ApplicationStatusHistory
    ApplicationStatusHistory.objects.create(application=app, status=app.status, comment=reason, by_user=request.user)
    # Email user with reason and link to edit
    try:
        from django.urls import reverse
        link = request.build_absolute_uri(reverse('application_fill', args=[app.booking_id]))
        send_mail(
            subject="PG-MS: Application Rejected",
            message=f"Your resident application was rejected for the following reason:\n\n{reason}\n\nPlease re-fill the form here: {link}",
            from_email=None,
            recipient_list=[app.user.email],
            fail_silently=False,
        )
    except Exception as e:
        messages.error(request, f"Failed to send rejection email: {e}")
    messages.info(request, "Application rejected and user notified.")
    return redirect('pg_resident_applications')


@login_required
@transaction.atomic
def application_refill_request(request, app_id):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    app = get_object_or_404(ResidentApplication, pk=app_id)
    if not _admin_pgs(request.user).filter(id=getattr(app, 'pg_id', None)).exists():
        messages.error(request, "PG Admin access required for this PG.")
        return redirect('dashboard')
    comment = (request.POST.get('comment') or '').strip()
    app.status = ResidentApplication.REFILL_REQUESTED
    app.save(update_fields=['status'])
    from bookings.models import ApplicationStatusHistory
    ApplicationStatusHistory.objects.create(application=app, status=app.status, comment=comment, by_user=request.user)
    # Email user to edit (this is commonly triggered after confirmation to request changes)
    try:
        from django.urls import reverse
        link = request.build_absolute_uri(reverse('application_fill', args=[app.booking_id]))
        send_mail(
            subject="PG-MS: Application Update Requested",
            message=f"Updates are requested on your resident application. Please edit here: {link}\n\nDetails: {comment or 'Please update your details as discussed.'}",
            from_email=None,
            recipient_list=[app.user.email],
            fail_silently=False,
        )
    except Exception as e:
        messages.error(request, f"Failed to send update request email: {e}")
    messages.success(request, "Re-Fill request sent to user.")
    return redirect('pg_resident_applications')
