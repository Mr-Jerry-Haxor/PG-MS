from django.db import models
from django.db.models import Q
from django.conf import settings
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

	user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bookings')
	room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='bookings')
	# Denormalized for constraints and fast queries: PG of the room
	pg = models.ForeignKey(PG, on_delete=models.CASCADE, related_name='bookings', null=True, blank=True)
	share_no = models.PositiveSmallIntegerField()
	status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
	advance_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	start_date = models.DateField(null=True, blank=True)
	joining_date = models.DateField(null=True, blank=True)
	leaving_date = models.DateField(null=True, blank=True)
	leaving_confirmed_date = models.DateField(null=True, blank=True)

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

	def __str__(self):
		return f"Application for {self.user.email} ({self.booking_id})"


class ApplicationStatusHistory(TimeStampedModel):
	application = models.ForeignKey(ResidentApplication, on_delete=models.CASCADE, related_name='status_history')
	status = models.CharField(max_length=20, choices=ResidentApplication.STATUS_CHOICES)
	comment = models.TextField(blank=True)
	by_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='application_status_actions')

	def __str__(self):
		return f"{self.application_id} -> {self.status}"

# Create your models here.
