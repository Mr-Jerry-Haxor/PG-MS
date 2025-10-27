from django.db import models
from django.db.models import Q
from django.conf import settings
from django.utils import timezone
from core.models import TimeStampedModel
from pgadmin.models import PG
from django.conf import settings


class Room(TimeStampedModel):
	pg = models.ForeignKey(PG, on_delete=models.CASCADE, related_name='rooms')
	room_no = models.CharField(max_length=20)
	total_shares = models.PositiveSmallIntegerField(default=1)

	class Meta:
		unique_together = ('pg', 'room_no')

	def __str__(self):
		return f"{self.pg.name} - {self.room_no}"


class RoomShareStatus(TimeStampedModel):
	VACANT = 'vacant'
	RESERVED = 'reserved'
	OCCUPIED = 'occupied'
	VACANT_FROM = 'vacant_from'
	STATUS_CHOICES = [
		(VACANT, 'Vacant'),
		(RESERVED, 'Reserved'),
		(OCCUPIED, 'Occupied'),
		(VACANT_FROM, 'Vacant From'),
	]
	room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='shares')
	share_no = models.PositiveSmallIntegerField()
	status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=VACANT)
	vacant_from = models.DateField(null=True, blank=True, help_text='Date from which the share will become vacant (post confirmed leaving).')

	class Meta:
		unique_together = ('room', 'share_no')

	def __str__(self):
		return f"{self.room} - Share {self.share_no}: {self.status}"


class Booking(TimeStampedModel):
	PENDING = 'pending'
	APPROVED = 'approved'
	REJECTED = 'rejected'
	COMPLETED = 'completed'
	STATUS_CHOICES = [
		(PENDING, 'Pending'),
		(APPROVED, 'Approved'),
		(REJECTED, 'Rejected'),
		(COMPLETED, 'Completed'),
	]
	
	# Booking type choices
	REGULAR = 'regular'
	DAYWISE = 'daywise'
	BOOKING_TYPE_CHOICES = [
		(REGULAR, 'Regular Monthly'),
		(DAYWISE, 'Day-wise/Short-term'),
	]

	user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bookings')
	room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='bookings')
	# Denormalized for constraints and fast queries: PG of the room
	pg = models.ForeignKey(PG, on_delete=models.CASCADE, related_name='bookings', null=True, blank=True)
	share_no = models.PositiveSmallIntegerField()
	booking_type = models.CharField(max_length=10, choices=BOOKING_TYPE_CHOICES, default=REGULAR, help_text="Type of booking: regular monthly or day-wise short-term")
	status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
	advance_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	start_date = models.DateField(null=True, blank=True)
	joining_date = models.DateField(null=True, blank=True, help_text="Date tenant actually moved in; used to calculate rent days. For day-wise bookings, this is the start date.")
	payment_date = models.DateField(null=True, blank=True, help_text="Monthly rent due date (day of month); defaults to joining date.")
	leaving_date = models.DateField(null=True, blank=True, help_text="Date tenant left or will leave. For day-wise bookings, this is the end date.")
	leaving_confirmed_date = models.DateField(null=True, blank=True)
	
	# Day-wise booking specific fields
	start_time = models.TimeField(null=True, blank=True, help_text="For day-wise bookings: check-in time")
	end_time = models.TimeField(null=True, blank=True, help_text="For day-wise bookings: check-out time")
	purpose = models.TextField(blank=True, help_text="For day-wise bookings: purpose of short stay")
	payment_received = models.BooleanField(default=False, help_text="For day-wise bookings: payment received status")
	payment_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="For day-wise bookings: specific payment amount")
	assigned_at = models.DateTimeField(null=True, blank=True, help_text="When room was assigned by admin")
	assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_bookings', help_text="Admin who assigned the room")
	
	# Enhanced leave management fields
	leaving_initiated_at = models.DateTimeField(null=True, blank=True, help_text="When user requested to leave")
	leaving_reason = models.TextField(blank=True, help_text="User's reason for leaving")
	advance_eligible = models.BooleanField(default=True, help_text="Eligible for advance refund based on notice period")
	advance_returned = models.BooleanField(default=False, help_text="Advance amount returned by PG admin")
	advance_returned_at = models.DateTimeField(null=True, blank=True)
	advance_returned_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

	class Meta:
		constraints = [
			# One active (pending/approved) booking per user per PG
			models.UniqueConstraint(
				fields=['user', 'pg'],
				condition=Q(status__in=['pending', 'approved']),
				name='uniq_active_booking_per_user_per_pg',
			),
			# Prevent multiple simultaneous pending/approved bookings for the same exact share by a user
			models.UniqueConstraint(
				fields=['room', 'share_no', 'user'],
				condition=Q(status__in=['pending', 'approved']),
				name='uniq_active_booking_same_share_user',
			),
		]

	def __str__(self):
		return f"{self.user} - {self.room} ({self.share_no}) [{self.status}]"

	def save(self, *args, **kwargs):
		# Keep denormalized pg in sync with room.pg
		if self.room_id:
			room_pg_id = getattr(self.room, 'pg_id', None)
			if room_pg_id and self.pg_id != room_pg_id:
				self.pg_id = room_pg_id
		
		# Sync joining_date and payment_date when one is missing
		# Priority: joining_date is the source of truth for when tenant moved in
		# payment_date should default to joining_date if not explicitly set
		if not self.joining_date and self.payment_date:
			# If only payment_date is set, use it as joining_date too
			# This handles legacy data where payment_date was set but joining_date wasn't
			self.joining_date = self.payment_date
		elif not self.payment_date:
			# Default payment date to joining/start date when missing
			anchor = self.joining_date or self.start_date
			if not anchor and getattr(self, 'created_at', None):
				created = self.created_at
				if created:
					if timezone.is_aware(created):
						created = timezone.localtime(created)
					anchor = created.date()
			if anchor:
				self.payment_date = anchor
		super().save(*args, **kwargs)


class ResidentApplication(TimeStampedModel):
	SUBMITTED = 'submitted'
	CONFIRMED = 'confirmed'
	REFILL_REQUESTED = 'refill_requested'
	RESUBMITTED = 'resubmitted'
	REJECTED = 'rejected'
	STATUS_CHOICES = [
		(SUBMITTED, 'Submitted'),
		(CONFIRMED, 'Confirmed'),
		(REFILL_REQUESTED, 'Re-Fill Requested'),
		(RESUBMITTED, 'Re-Submitted'),
		(REJECTED, 'Rejected'),
	]
	user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='resident_applications')
	booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='application')
	pg = models.ForeignKey(PG, on_delete=models.CASCADE, related_name='resident_applications')
	room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='resident_applications')
	status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=SUBMITTED)
	# Personal details
	name = models.CharField(max_length=255)
	dob = models.DateField(null=True, blank=True)
	age = models.PositiveSmallIntegerField(null=True, blank=True)
	phone = models.CharField(max_length=20)
	emergency_contact = models.CharField(max_length=20, blank=True, help_text="Emergency contact number (for day-wise bookings)")
	whatsapp_number = models.CharField(max_length=20, blank=True, help_text="WhatsApp number (can be same as phone)")
	email = models.EmailField()
	father_name = models.CharField(max_length=255, blank=True)
	father_phone = models.CharField(max_length=20, blank=True)
	mother_name = models.CharField(max_length=255, blank=True)
	mother_phone = models.CharField(max_length=20, blank=True)
	address = models.TextField(blank=True)
	date_of_admission = models.DateField(null=True, blank=True)
	food_pref = models.CharField(max_length=10, choices=[('veg','Veg'),('nonveg','Non-Veg')], blank=True)
	marital_status = models.CharField(max_length=10, choices=[('single','Single'),('married','Married')], blank=True)
	education = models.CharField(max_length=255, blank=True)
	occupation = models.CharField(max_length=20, choices=[('student','Student'),('employee','Employee')], blank=True)
	org_name = models.CharField(max_length=255, blank=True)  # company/college
	org_address = models.TextField(blank=True)
	aadhaar_number = models.CharField(max_length=20, blank=True)
	selfie_url = models.URLField(blank=True)
	aadhaar_file_url = models.URLField(blank=True)
	# Optional second image URL when user uploads front/back images instead of a PDF
	aadhaar_file_url_2 = models.URLField(blank=True)
	# Vehicle details
	has_vehicle = models.BooleanField(default=False)
	vehicle_number = models.CharField(max_length=32, blank=True)
	vehicle_model = models.CharField(max_length=100, blank=True)
	# Declarations
	decl_valuables = models.BooleanField(default=False)
	decl_notice = models.BooleanField(default=False)
	decl_deposit = models.BooleanField(default=False)
	decl_truth = models.BooleanField(default=False)
	referred_by_booking = models.ForeignKey('Booking', null=True, blank=True, on_delete=models.SET_NULL, related_name='referrals_made', help_text='Booking of the resident who referred this applicant (set by PG admin).')

	def __str__(self):
		return f"Application for {self.user.email} ({self.booking_id})"


class ApplicationStatusHistory(TimeStampedModel):
	application = models.ForeignKey(ResidentApplication, on_delete=models.CASCADE, related_name='status_history')
	status = models.CharField(max_length=20, choices=ResidentApplication.STATUS_CHOICES)
	comment = models.TextField(blank=True)
	by_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='application_status_actions')

	def __str__(self):
		return f"{self.application_id} -> {self.status}"


class ReferralCredit(TimeStampedModel):
	pg = models.ForeignKey(PG, on_delete=models.CASCADE, related_name='referral_credits')
	referrer_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='referral_credits_given')
	referrer_booking = models.ForeignKey('Booking', on_delete=models.SET_NULL, null=True, blank=True, related_name='referral_credits_source')
	referred_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='referral_credits_received')
	referred_booking = models.ForeignKey('Booking', on_delete=models.SET_NULL, null=True, blank=True, related_name='referral_credit_target')
	application = models.OneToOneField(ResidentApplication, on_delete=models.CASCADE, related_name='referral_credit')
	amount = models.DecimalField(max_digits=10, decimal_places=2)
	scheduled_month = models.DateField(null=True, blank=True, help_text='First day of month when this credit should be applied.')
	redeemed_for_month = models.DateField(null=True, blank=True, help_text='First day of the month where the credit was applied.')
	redeemed_on = models.DateTimeField(null=True, blank=True)
	redeemed_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
	notes = models.CharField(max_length=255, blank=True)

	class Meta:
		ordering = ['-created_at']

	def __str__(self):
		return f"Referral credit ₹{self.amount} for {self.referrer_user}"

# Create your models here.


# Keep ResidentApplication.name in sync with the related User's first_name/last_name.
# When an application's name is changed, copy it to user.first_name and clear user.last_name.
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model


@receiver(post_save, sender=ResidentApplication)
def sync_application_name_to_user(sender, instance: ResidentApplication, created, **kwargs):
	"""If the ResidentApplication.name differs from the user's first_name, update the user.

	Behavior:
	- On create or update, if instance.name is non-empty and not equal to user.first_name,
	  set user.first_name = instance.name and user.last_name = '' then save the user.
	- This keeps the profile display name in sync when PG admins or users edit the application name.
	"""
	try:
		User = get_user_model()
		user = instance.user
		if not user:
			return
		new_first = (instance.name or '').strip()
		old_first = (getattr(user, 'first_name', '') or '').strip()
		if new_first and new_first != old_first:
			user.first_name = new_first
			user.last_name = ''
			user.save(update_fields=['first_name', 'last_name'])
	except Exception:
		# Avoid raising from signal; logging can be added if needed.
		pass


class RoomSwap(TimeStampedModel):
	"""Model for room swap requests, including future swaps based on leaving dates."""
	PENDING = 'pending'
	APPROVED = 'approved'
	REJECTED = 'rejected'
	COMPLETED = 'completed'
	CANCELLED = 'cancelled'
	
	STATUS_CHOICES = [
		(PENDING, 'Pending'),
		(APPROVED, 'Approved'),
		(REJECTED, 'Rejected'),
		(COMPLETED, 'Completed'),
		(CANCELLED, 'Cancelled'),
	]
	
	booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='swaps', help_text="The booking that is being swapped")
	from_room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='swaps_from')
	from_share_no = models.PositiveSmallIntegerField()
	to_room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='swaps_to')
	to_share_no = models.PositiveSmallIntegerField()
	effective_date = models.DateField(help_text="Date when swap takes effect")
	is_future_swap = models.BooleanField(default=False, help_text="Swap scheduled for future based on leaving date")
	status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
	reason = models.TextField(blank=True)
	requested_at = models.DateTimeField(auto_now_add=True)
	processed_at = models.DateTimeField(null=True, blank=True)
	processed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='processed_swaps')
	
	class Meta:
		ordering = ['-requested_at']
	
	def __str__(self):
		return f"{self.booking.user} swap from {self.from_room.room_no}/{self.from_share_no} to {self.to_room.room_no}/{self.to_share_no} on {self.effective_date}"

