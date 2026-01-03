from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from .models import Notification
from bookings.models import RoomShareStatus, Booking, ReferralCredit, RoomSwap
from finance.models import Payment, Expenditure
from django.utils import timezone
from django.db.models import Sum, Q
from pgadmin.models import PG, Complaint
from datetime import date as _date
from datetime import datetime as _datetime
import logging

logger = logging.getLogger(__name__)


def _auto_sync_pg_on_dashboard(pg, user):
    """
    Auto-sync bed statuses and execute pending future swaps for a PG.
    Called silently when a PG admin opens the dashboard.
    This keeps bed statuses accurate without requiring manual Refresh.
    """
    if not pg:
        return {'synced': False, 'swaps_executed': 0, 'swaps_failed': 0}
    
    today = timezone.now().date()
    results = {'synced': False, 'swaps_executed': 0, 'swaps_failed': 0}
    
    try:
        # 1. Execute pending future swaps that are due
        from bookings.models import RoomSwap
        pending_swaps = RoomSwap.objects.filter(
            status=RoomSwap.PENDING,
            is_future_swap=True,
            effective_date__lte=today,
            booking__room__pg=pg
        ).select_related('booking', 'from_room', 'to_room', 'booking__user').order_by('effective_date', 'requested_at')
        
        for swap in pending_swaps:
            try:
                result = _execute_future_swap_silent(swap, user)
                if result['success']:
                    results['swaps_executed'] += 1
                else:
                    results['swaps_failed'] += 1
            except Exception as e:
                logger.warning(f"Auto-execute swap #{swap.id} failed: {e}")
                results['swaps_failed'] += 1
        
        # 2. Sync bed statuses
        from bookings.utils import sync_room_share_statuses
        sync_room_share_statuses(pg=pg)
        results['synced'] = True
        
    except Exception as e:
        logger.warning(f"Auto-sync for PG {pg.id} failed: {e}")
    
    return results


def _execute_future_swap_silent(swap, executor):
    """
    Execute a pending future swap silently (no messages).
    Returns: {'success': bool, 'error': str or None}
    """
    from django.db import transaction
    from bookings.models import Room, RoomShareStatus, Booking, RoomSwap
    from core.audit import log
    
    try:
        with transaction.atomic():
            # Re-fetch with lock
            swap = RoomSwap.objects.select_for_update().get(pk=swap.id)
            
            if swap.status != RoomSwap.PENDING:
                return {'success': False, 'error': f'Swap status is {swap.status}'}
            
            booking = Booking.objects.select_for_update().get(pk=swap.booking_id, status=Booking.APPROVED)
            from_room = Room.objects.select_for_update().get(pk=swap.from_room_id)
            to_room = Room.objects.select_for_update().get(pk=swap.to_room_id)
            from_share = RoomShareStatus.objects.select_for_update().get(room=from_room, share_no=swap.from_share_no)
            to_share = RoomShareStatus.objects.select_for_update().get(room=to_room, share_no=swap.to_share_no)
            
            # Verify booking is still at source location
            if booking.room_id != from_room.id or booking.share_no != swap.from_share_no:
                swap.status = RoomSwap.CANCELLED
                swap.reason += f" | Auto-cancelled: booking moved"
                swap.processed_at = timezone.now()
                swap.save(update_fields=['status', 'reason', 'processed_at'])
                return {'success': False, 'error': 'Booking moved'}
            
            today = timezone.now().date()
            
            # Check if target bed is actually available (by checking bookings, not status)
            blocking_booking = Booking.objects.filter(
                room=to_room,
                share_no=swap.to_share_no,
                status=Booking.APPROVED,
                joining_date__lte=today
            ).filter(
                Q(leaving_date__isnull=True) | Q(leaving_date__gt=today)
            ).exclude(pk=booking.pk).first()
            
            if blocking_booking:
                swap.status = RoomSwap.CANCELLED
                blocker_name = blocking_booking.user.get_full_name() or blocking_booking.user.email
                swap.reason += f" | Auto-cancelled: occupied by {blocker_name}"
                swap.processed_at = timezone.now()
                swap.save(update_fields=['status', 'reason', 'processed_at'])
                return {'success': False, 'error': f'Bed occupied by {blocker_name}'}
            
            # Execute the swap
            booking.room = to_room
            booking.share_no = swap.to_share_no
            try:
                booking.pg = to_room.pg
            except Exception:
                pass
            booking.save(update_fields=['room', 'pg', 'share_no'])
            
            # Update application if exists
            app = getattr(booking, 'application', None)
            if app and app.room_id != to_room.id:
                app.room = to_room
                app.save(update_fields=['room'])
            
            # Update share statuses
            from_share.status = RoomShareStatus.VACANT
            from_share.vacant_from = None
            from_share.save(update_fields=['status', 'vacant_from'])
            
            to_share.status = RoomShareStatus.OCCUPIED
            to_share.vacant_from = None
            to_share.save(update_fields=['status', 'vacant_from'])
            
            # Mark swap completed
            swap.status = RoomSwap.COMPLETED
            swap.processed_at = timezone.now()
            swap.processed_by = executor
            swap.save(update_fields=['status', 'processed_at', 'processed_by'])
            
            # Log
            log(
                executor,
                'future_swap_auto_executed',
                'RoomSwap',
                swap.id,
                f"Auto-executed swap: {booking.user.get_full_name() or booking.user.email} from room {from_room.room_no} bed {swap.from_share_no} to room {to_room.room_no} bed {swap.to_share_no}"
            )
            
            return {'success': True, 'error': None}
            
    except Booking.DoesNotExist:
        return {'success': False, 'error': 'Booking not found'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


@login_required
def dashboard(request):
	# Load top unread notifications to display on dashboard
	notes_qs = Notification.objects.filter(user=request.user, is_read=False).order_by('-created_at')[:10]
	# Evaluate to list so we can safely mark them as read while still rendering these items
	notes = list(notes_qs)
	ctx = {"notifications": notes}
	
	# Load advertisements for regular PG users (non-admin)
	# Get the user's current PG from their active booking
	try:
		from advertisements.models import AdvertisementSettings, AdvertisementImage, AdvertisementText
		user_pg = None
		# Get user's active booking to determine their PG
		active_booking = Booking.objects.filter(
			user=request.user,
			status=Booking.APPROVED
		).filter(
			Q(leaving_date__isnull=True) | Q(leaving_date__gte=_date.today())
		).select_related('room__pg').first()
		
		if active_booking:
			user_pg = active_booking.room.pg
		
		if user_pg:
			# Get advertisement settings
			ad_settings = AdvertisementSettings.objects.filter(pg=user_pg).first()
			if ad_settings:
				ctx['ad_settings'] = ad_settings
				# Get active carousel images
				if ad_settings.carousel_enabled:
					carousel_images = AdvertisementImage.objects.filter(
						pg=user_pg, is_active=True
					).order_by('order')
					ctx['carousel_images'] = list(carousel_images)
				# Get active scrolling texts
				if ad_settings.text_enabled:
					ad_texts = AdvertisementText.objects.filter(
						pg=user_pg, is_active=True
					).order_by('order')
					ctx['ad_texts'] = list(ad_texts)
	except ImportError:
		# advertisements app not installed yet
		pass
	except Exception as e:
		logger.warning(f"Error loading advertisements: {e}")
	
	# Mark only these displayed notifications as read so the unread badge updates immediately
	if notes:
		Notification.objects.filter(id__in=[n.id for n in notes], user=request.user, is_read=False).update(is_read=True)

	ctx["today"] = _date.today()
	today = _date.today()
	
	# Initialize employee permission flags (will be overridden if PG admin)
	ctx["can_view_employees"] = False
	ctx["can_edit_employees"] = False
	
	# Always show user's bookings on dashboard (approved/pending/completed)
	approved_qs = (
		Booking.objects.filter(user=request.user, status=Booking.APPROVED)
		.select_related('room', 'room__pg')
		.prefetch_related('application')
		.order_by('-created_at')
	)
	pending_qs = (
		Booking.objects.filter(user=request.user, status=Booking.PENDING)
		.select_related('room', 'room__pg')
		.prefetch_related('application')
		.order_by('-created_at')
	)
	my_booking = approved_qs.first() or pending_qs.first()
	ctx["my_booking"] = my_booking
	ctx["my_pending_bookings"] = list(pending_qs)
	ctx["my_approved_bookings"] = list(approved_qs)
	completed_qs = (
		Booking.objects.filter(user=request.user, status=Booking.COMPLETED)
		.select_related('room', 'room__pg')
		.prefetch_related('application')
		.order_by('-updated_at')[:5]
	)
	ctx["my_completed_bookings"] = list(completed_qs)

	# Attach day-wise summary fields to bookings passed to the template
	def _attach_daywise_summary(bk):
		# Only for day-wise bookings with necessary fields
		try:
			if getattr(bk, 'booking_type', None) == Booking.DAYWISE:
				start_date = getattr(bk, 'joining_date', None) or getattr(bk, 'start_date', None)
				end_date = getattr(bk, 'leaving_date', None)
				start_time = getattr(bk, 'start_time', None)
				end_time = getattr(bk, 'end_time', None)
				if start_date and end_date and start_time and end_time:
					start_dt = _datetime.combine(start_date, start_time)
					end_dt = _datetime.combine(end_date, end_time)
					# Avoid negative durations
					delta = end_dt - start_dt
					hours = max(0, int(delta.total_seconds() / 3600))
					bk.daywise_summary = f"{start_dt.strftime('%b %d, %Y %I:%M %p')} - {end_dt.strftime('%b %d, %Y %I:%M %p')} ({hours} hours)"
					bk.daywise_total_hours = hours
				else:
					bk.daywise_summary = ''
					bk.daywise_total_hours = 0
		except Exception:
			# Be defensive: don't break rendering if anything unexpected
			bk.daywise_summary = ''
			bk.daywise_total_hours = 0
		return bk

	# Apply to lists that will be rendered
	if ctx.get('my_booking'):
		ctx['my_booking'] = _attach_daywise_summary(ctx['my_booking'])
	ctx['my_pending_bookings'] = [_attach_daywise_summary(b) for b in ctx.get('my_pending_bookings', [])]
	ctx['my_approved_bookings'] = [_attach_daywise_summary(b) for b in ctx.get('my_approved_bookings', [])]
	ctx['my_completed_bookings'] = [_attach_daywise_summary(b) for b in ctx.get('my_completed_bookings', [])]
	
	# Get active bookings (not left yet) for complaint button
	active_bookings = Booking.objects.filter(
		user=request.user,
		status=Booking.APPROVED
	).filter(
		Q(leaving_date__isnull=True) | Q(leaving_date__gte=today)
	).select_related('pg', 'room')
	
	ctx["has_active_booking"] = active_bookings.exists()
	ctx["active_bookings_count"] = active_bookings.count()
	ctx["active_bookings"] = list(active_bookings)
	
	# Get user's complaints for active bookings
	if active_bookings.exists():
		user_complaints = Complaint.objects.filter(
			user=request.user,
			booking__in=active_bookings
		).select_related('pg', 'booking').prefetch_related(
			'comments'
		).order_by('-created_at')[:5]
		
		# Add public comment count to each complaint
		complaints_list = []
		for complaint in user_complaints:
			# Count public comments (non-internal)
			complaint.public_comment_count = complaint.comments.filter(is_internal=False).count()
			complaints_list.append(complaint)
		
		ctx["user_complaints"] = complaints_list
	
	if hasattr(request.user, 'profile') and request.user.profile.is_pg_admin:
		# Determine PGs this admin can manage and active selection
		pgs_qs = PG.objects.filter(admins__user=request.user).order_by('name')
		pg = None
		pg_id = request.GET.get('pg') or request.session.get('active_pg_id')
		if pg_id:
			# Enforce: can only switch to PGs you manage
			pg = pgs_qs.filter(id=pg_id).first()
		if not pg:
			pg = pgs_qs.first()
		if pg:
			request.session['active_pg_id'] = pg.id
			
			# AUTO-SYNC: Silently execute pending future swaps and sync bed statuses
			# This ensures beds are always up-to-date when admin views dashboard
			try:
				sync_result = _auto_sync_pg_on_dashboard(pg, request.user)
				# Optionally show a message if swaps were executed
				if sync_result.get('swaps_executed', 0) > 0:
					from django.contrib import messages
					messages.success(
						request,
						f"Auto-executed {sync_result['swaps_executed']} scheduled room swap(s)."
					)
			except Exception as e:
				logger.warning(f"Dashboard auto-sync failed: {e}")
		
		# Metrics for selected PG (or across all if none)
		today = timezone.now().date()
		month_start = today.replace(day=1)
		# Compute first day of next month for upper bound
		if month_start.month == 12:
			next_month_start = month_start.replace(year=month_start.year + 1, month=1, day=1)
		else:
			next_month_start = month_start.replace(month=month_start.month + 1, day=1)
		if pg:
			vacant = RoomShareStatus.objects.filter(status=RoomShareStatus.VACANT, room__pg=pg).count()
			reserved = RoomShareStatus.objects.filter(status=RoomShareStatus.RESERVED, room__pg=pg).count()
			occupied = RoomShareStatus.objects.filter(status=RoomShareStatus.OCCUPIED, room__pg=pg).count()
			leaving = RoomShareStatus.objects.filter(status=RoomShareStatus.VACANT_FROM, room__pg=pg).count()
			total = RoomShareStatus.objects.filter(room__pg=pg).count()
			pending = Booking.objects.filter(status=Booking.PENDING, room__pg=pg).count()
			# Leaving stats (approved bookings with a leaving_date)
			leaving_qs = Booking.objects.filter(room__pg=pg, status=Booking.APPROVED, leaving_date__isnull=False)
			leaving_pending = leaving_qs.filter(leaving_confirmed_date__isnull=True).count()
			leaving_confirmed = leaving_qs.filter(leaving_confirmed_date__isnull=False).count()
			income = (
				Payment.objects.filter(pg=pg, date__gte=month_start, date__lt=next_month_start).aggregate(total=Sum('amount')).get('total') or 0
			)
			expense = (
				Expenditure.objects.filter(pg=pg, date__gte=month_start, date__lt=next_month_start).aggregate(total=Sum('amount')).get('total') or 0
			)
		else:
			vacant = RoomShareStatus.objects.filter(status=RoomShareStatus.VACANT, room__pg__admins__user=request.user).count()
			reserved = RoomShareStatus.objects.filter(status=RoomShareStatus.RESERVED, room__pg__admins__user=request.user).count()
			occupied = RoomShareStatus.objects.filter(status=RoomShareStatus.OCCUPIED, room__pg__admins__user=request.user).count()
			leaving = RoomShareStatus.objects.filter(status=RoomShareStatus.VACANT_FROM, room__pg__admins__user=request.user).count()
			total = RoomShareStatus.objects.filter(room__pg__admins__user=request.user).count()
			pending = Booking.objects.filter(status=Booking.PENDING, room__pg__admins__user=request.user).count()
			leaving_qs = Booking.objects.filter(room__pg__admins__user=request.user, status=Booking.APPROVED, leaving_date__isnull=False)
			leaving_pending = leaving_qs.filter(leaving_confirmed_date__isnull=True).count()
			leaving_confirmed = leaving_qs.filter(leaving_confirmed_date__isnull=False).count()
			income = (
				Payment.objects.filter(pg__admins__user=request.user, date__gte=month_start, date__lt=next_month_start).aggregate(total=Sum('amount')).get('total') or 0
			)
			expense = (
				Expenditure.objects.filter(pg__admins__user=request.user, date__gte=month_start, date__lt=next_month_start).aggregate(total=Sum('amount')).get('total') or 0
			)
		
		# Get complaints count for admin
		if pg:
			complaints_open = Complaint.objects.filter(pg=pg, status=Complaint.OPEN).count()
			complaints_in_progress = Complaint.objects.filter(pg=pg, status=Complaint.IN_PROGRESS).count()
		else:
			complaints_open = Complaint.objects.filter(pg__admins__user=request.user, status=Complaint.OPEN).count()
			complaints_in_progress = Complaint.objects.filter(pg__admins__user=request.user, status=Complaint.IN_PROGRESS).count()
		
		ctx.update({
			"vacant_beds": vacant,
			"reserved_beds": reserved,
			"occupied_beds": occupied,
			"pending_bookings": pending,
			"leaving_pending": leaving_pending,
			"leaving_confirmed": leaving_confirmed,
			"month_income": income,
			"month_expense": expense,
			"complaints_open": complaints_open,
			"complaints_in_progress": complaints_in_progress,
			"pg": pg,
			"pgs": list(pgs_qs),
			# Legacy aliases maintained until template migration completes everywhere
			"vacant_shares": vacant,
			"reserved_shares": reserved,
			"occupied_shares": occupied,
			"leaving_shares": leaving,
			"total_shares": total,
		})
	
	# Check if user (PG admin) has employee access permission
	# This runs for ANY user who is actually a PG Admin (via PGAdmin records)
	# regardless of whether profile.is_pg_admin flag is set
	try:
		from pgadmin.models import PGAdmin, PGAdminPermission
		can_view = False
		can_edit = False
		
		# Check if user has permission on ANY of their PGs
		all_pg_admins = PGAdmin.objects.filter(user=request.user)
		for pa in all_pg_admins:
			perm = PGAdminPermission.objects.filter(pg_admin=pa).first()
			if perm and (perm.can_view_employees or perm.can_edit_employees):
				can_view = True
				if perm.can_edit_employees:
					can_edit = True
				break
		
		ctx["can_view_employees"] = can_view
		ctx["can_edit_employees"] = can_edit
	except Exception:
		pass  # Keep default False values set earlier

	# Include user's referrals so dashboard can show a compact view for referrers
	if request.user.is_authenticated:
		try:
			my_referrals_qs = ReferralCredit.objects.filter(referrer_user=request.user).select_related(
				'referrer_booking__room', 'referred_booking__room', 'referrer_user', 'referred_user', 'pg'
			).order_by('-created_at')
			ctx['my_referrals'] = list(my_referrals_qs)
		except Exception:
			# Be defensive: if referrals table or relations are missing, skip showing referrals
			ctx['my_referrals'] = []
	return render(request, 'dashboard.html', ctx)


@login_required
def notifications(request):
	items = Notification.objects.filter(user=request.user).order_by('-created_at')
	return render(request, 'notifications.html', {"items": items})


def home(request):
	"""Render home for anonymous users; redirect authenticated users to dashboard."""
	if request.user.is_authenticated:
		return redirect('dashboard')
	return render(request, 'home.html')

@login_required
def notification_read(request, pk):
	n = get_object_or_404(Notification, pk=pk, user=request.user)
	n.is_read = True
	n.save(update_fields=['is_read'])
	return redirect('notifications')


@login_required
def notifications_mark_all(request):
	Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
	return redirect('notifications')

# Create your views here.
