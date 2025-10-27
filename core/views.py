from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from .models import Notification
from bookings.models import RoomShareStatus, Booking
from finance.models import Payment, Expenditure
from django.utils import timezone
from django.db.models import Sum, Q
from pgadmin.models import PG, Complaint
from datetime import date as _date
from datetime import datetime as _datetime


@login_required
def dashboard(request):
	# Load top unread notifications to display on dashboard
	notes_qs = Notification.objects.filter(user=request.user, is_read=False).order_by('-created_at')[:10]
	# Evaluate to list so we can safely mark them as read while still rendering these items
	notes = list(notes_qs)
	ctx = {"notifications": notes}
	# Mark only these displayed notifications as read so the unread badge updates immediately
	if notes:
		Notification.objects.filter(id__in=[n.id for n in notes], user=request.user, is_read=False).update(is_read=True)

	ctx["today"] = _date.today()
	today = _date.today()
	
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
