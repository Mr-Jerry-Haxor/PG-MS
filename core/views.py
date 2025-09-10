from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from .models import Notification
from bookings.models import RoomShareStatus, Booking
from finance.models import Payment, Expenditure
from django.utils import timezone
from django.db.models import Sum
from pgadmin.models import PG


@login_required
def dashboard(request):
	notes = Notification.objects.filter(user=request.user, is_read=False).order_by('-created_at')[:10]
	ctx = {"notifications": notes}
	from datetime import date as _date
	ctx["today"] = _date.today()
	# For PG users, show their current booking details on dashboard
	if hasattr(request.user, 'profile') and request.user.profile.is_pg_user:
		approved_qs = (
			Booking.objects.filter(user=request.user, status=Booking.APPROVED)
			.select_related('room', 'room__pg')
			.prefetch_related('application')
			.order_by('-created_at')
		)
		pending_qs = (
			Booking.objects.filter(user=request.user, status=Booking.PENDING)
			.select_related('room', 'room__pg')
			.order_by('-created_at')
		)
		my_booking = approved_qs.first() or pending_qs.first()
		ctx["my_booking"] = my_booking
		ctx["my_pending_bookings"] = list(pending_qs)
		ctx["my_approved_bookings"] = list(approved_qs)
		completed_qs = (
			Booking.objects.filter(user=request.user, status=Booking.COMPLETED)
			.select_related('room', 'room__pg')
			.order_by('-updated_at')[:5]
		)
		ctx["my_completed_bookings"] = list(completed_qs)
	if hasattr(request.user, 'profile') and request.user.profile.is_pg_admin:
		# Determine PGs this admin can manage and active selection
		pgs_qs = PG.objects.filter(admins__user=request.user).order_by('name')
		pg = None
		pg_id = request.GET.get('pg') or request.session.get('active_pg_id')
		if pg_id:
			pg = pgs_qs.filter(id=pg_id).first()
		if not pg:
			pg = pgs_qs.first()
		if pg:
			request.session['active_pg_id'] = pg.id
		# Metrics for selected PG (or across all if none)
		today = timezone.now().date()
		month_start = today.replace(day=1)
		if pg:
			vacant = RoomShareStatus.objects.filter(status=RoomShareStatus.VACANT, room__pg=pg).count()
			occupied = RoomShareStatus.objects.filter(status=RoomShareStatus.OCCUPIED, room__pg=pg).count()
			pending = Booking.objects.filter(status=Booking.PENDING, room__pg=pg).count()
			income = (
				Payment.objects.filter(pg=pg, date__gte=month_start).aggregate(total=Sum('amount')).get('total') or 0
			)
			expense = (
				Expenditure.objects.filter(pg=pg, date__gte=month_start).aggregate(total=Sum('amount')).get('total') or 0
			)
		else:
			vacant = RoomShareStatus.objects.filter(status=RoomShareStatus.VACANT, room__pg__admins__user=request.user).count()
			occupied = RoomShareStatus.objects.filter(status=RoomShareStatus.OCCUPIED, room__pg__admins__user=request.user).count()
			pending = Booking.objects.filter(status=Booking.PENDING, room__pg__admins__user=request.user).count()
			income = (
				Payment.objects.filter(pg__admins__user=request.user, date__gte=month_start).aggregate(total=Sum('amount')).get('total') or 0
			)
			expense = (
				Expenditure.objects.filter(pg__admins__user=request.user, date__gte=month_start).aggregate(total=Sum('amount')).get('total') or 0
			)
		ctx.update({
			"vacant_shares": vacant,
			"occupied_shares": occupied,
			"pending_bookings": pending,
			"month_income": income,
			"month_expense": expense,
			"pg": pg,
			"pgs": list(pgs_qs),
		})
	return render(request, 'dashboard.html', ctx)


@login_required
def notifications(request):
	items = Notification.objects.filter(user=request.user).order_by('-created_at')
	return render(request, 'notifications.html', {"items": items})


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
