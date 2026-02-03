from django.db import models
from django.contrib.auth import get_user_model
from core.models import TimeStampedModel
from django.utils.text import slugify


class PG(TimeStampedModel):
	name = models.CharField(max_length=200)
	slug = models.SlugField(max_length=200, unique=True, blank=True)
	address = models.TextField()
	# Optional contact phone number for the PG (10-15 digits to allow country codes)
	phone = models.CharField(max_length=20, blank=True, help_text="Contact phone number")
	created_by_admin = models.ForeignKey(get_user_model(), on_delete=models.SET_NULL, null=True, blank=True, related_name='created_pgs')
	referral_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Default referral credit amount for this PG.")
	# Allow PG admins to permit selecting past joining dates in quick booking flows
	past_joining_date_allowed = models.BooleanField(default=False, help_text="Allow selecting a joining date in the past for quick bookings.")
	# Allow tenants to choose a custom leaving date when submitting a leave request
	allow_custom_leave_date = models.BooleanField(default=True, blank=True, help_text="Allow tenants to select a custom leaving date when requesting to leave the PG.")
	# Notice period in days (default 30 days)
	notice_period = models.PositiveIntegerField(default=30, help_text="Notice period in days required before leaving.")
	# Day-wise booking fee per day
	daywise_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Fee per day for day-wise/short-term bookings.")
	# WhatsApp group invite settings
	whatsapp_invite_link = models.URLField(max_length=500, blank=True, null=True, help_text="WhatsApp group invite link for this PG.")
	whatsapp_invite_message = models.TextField(blank=True, null=True, help_text="Custom message to send with the WhatsApp group invite link.")

	def __str__(self):
		return self.name

	def save(self, *args, **kwargs):
		# Auto-generate a unique slug from name if missing or if name changed with empty slug
		if not self.slug and self.name:
			base = slugify(self.name) or 'pg'
			slug = base
			# Ensure uniqueness
			counter = 2
			Model = type(self)
			while Model.objects.filter(slug=slug).exclude(pk=self.pk).exists():
				slug = f"{base}-{counter}"
				counter += 1
			self.slug = slug
		super().save(*args, **kwargs)


class PGAdmin(TimeStampedModel):
	# Allow a single user to manage multiple PGs
	user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name='pg_admin_profile')
	pg = models.ForeignKey(PG, on_delete=models.CASCADE, related_name='admins')

	def __str__(self):
		return f"{self.user} ({self.pg})"


class PGAdminPermission(TimeStampedModel):
	"""
	Permissions model for PG Admins. Controls access to specific features.
	All permissions default to False.
	"""
	pg_admin = models.OneToOneField(PGAdmin, on_delete=models.CASCADE, related_name='permissions')
	
	# Employee Management
	can_view_employees = models.BooleanField(default=False, help_text='Can view employee details for their PG')
	can_edit_employees = models.BooleanField(default=False, help_text='Can create, edit and delete employees for their PG')
	
	# Payment Management
	can_delete_payments = models.BooleanField(default=False, help_text='Can delete payment entries')
	can_edit_payments = models.BooleanField(default=False, help_text='Can edit payment entries')
	
	# Application Management
	can_edit_applications = models.BooleanField(default=False, help_text='Can edit tenant applications for their PG')
	
	# Booking Management
	can_delete_confirmed_bookings = models.BooleanField(default=False, help_text='Can delete confirmed/approved bookings')
	
	class Meta:
		verbose_name = 'PG Admin Permission'
		verbose_name_plural = 'PG Admin Permissions'
	
	def __str__(self):
		return f"Permissions for {self.pg_admin}"
	
	@classmethod
	def get_or_create_for_admin(cls, pg_admin):
		"""Get or create permission record for a PG Admin"""
		perm, created = cls.objects.get_or_create(pg_admin=pg_admin)
		return perm


class Complaint(TimeStampedModel):
	"""
	Model for tenant complaints. Only users with active bookings can create complaints.
	"""
	# Status choices
	OPEN = 'open'
	IN_PROGRESS = 'in_progress'
	SOLVED = 'solved'
	
	STATUS_CHOICES = [
		(OPEN, 'Open'),
		(IN_PROGRESS, 'In Progress'),
		(SOLVED, 'Solved'),
	]
	
	# Priority choices
	LOW = 'low'
	MEDIUM = 'medium'
	HIGH = 'high'
	URGENT = 'urgent'
	
	PRIORITY_CHOICES = [
		(LOW, 'Low'),
		(MEDIUM, 'Medium'),
		(HIGH, 'High'),
		(URGENT, 'Urgent'),
	]
	
	# Category choices
	MAINTENANCE = 'maintenance'
	CLEANLINESS = 'cleanliness'
	FOOD = 'food'
	WIFI = 'wifi'
	ELECTRICITY = 'electricity'
	WATER = 'water'
	SECURITY = 'security'
	NOISE = 'noise'
	OTHER = 'other'
	
	CATEGORY_CHOICES = [
		(MAINTENANCE, 'Maintenance'),
		(CLEANLINESS, 'Cleanliness'),
		(FOOD, 'Food'),
		(WIFI, 'WiFi/Internet'),
		(ELECTRICITY, 'Electricity'),
		(WATER, 'Water Supply'),
		(SECURITY, 'Security'),
		(NOISE, 'Noise'),
		(OTHER, 'Other'),
	]
	
	# Foreign keys
	user = models.ForeignKey(
		get_user_model(), 
		on_delete=models.CASCADE, 
		related_name='complaints',
		help_text='User who raised the complaint'
	)
	pg = models.ForeignKey(
		PG, 
		on_delete=models.CASCADE, 
		related_name='complaints',
		help_text='PG where the complaint was raised'
	)
	booking = models.ForeignKey(
		'bookings.Booking', 
		on_delete=models.SET_NULL, 
		null=True, 
		blank=True,
		related_name='complaints',
		help_text='Active booking reference'
	)
	
	# Complaint details
	title = models.CharField(max_length=200, help_text='Brief title of the complaint')
	description = models.TextField(help_text='Detailed description of the issue')
	category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default=OTHER)
	priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default=MEDIUM)
	status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=OPEN)
	
	# Tracking
	resolved_at = models.DateTimeField(null=True, blank=True, help_text='When the complaint was marked as solved')
	resolved_by = models.ForeignKey(
		get_user_model(), 
		on_delete=models.SET_NULL, 
		null=True, 
		blank=True,
		related_name='resolved_complaints',
		help_text='Admin who resolved the complaint'
	)
	
	class Meta:
		ordering = ['-created_at']
		indexes = [
			models.Index(fields=['pg', 'status']),
			models.Index(fields=['user', 'status']),
			models.Index(fields=['created_at']),
		]
	
	def __str__(self):
		return f"{self.title} - {self.user.get_full_name() or self.user.email} ({self.status})"
	
	def get_status_badge_class(self):
		"""Return Bootstrap badge class for status"""
		return {
			self.OPEN: 'bg-danger',
			self.IN_PROGRESS: 'bg-warning text-dark',
			self.SOLVED: 'bg-success',
		}.get(self.status, 'bg-secondary')
	
	def get_priority_badge_class(self):
		"""Return Bootstrap badge class for priority"""
		return {
			self.LOW: 'bg-info',
			self.MEDIUM: 'bg-primary',
			self.HIGH: 'bg-warning text-dark',
			self.URGENT: 'bg-danger',
		}.get(self.priority, 'bg-secondary')
	
	def get_category_icon(self):
		"""Return icon class for category"""
		return {
			self.MAINTENANCE: 'bi-tools',
			self.CLEANLINESS: 'bi-stars',
			self.WIFI: 'bi-wifi',
			self.ELECTRICITY: 'bi-lightning-charge',
			self.WATER: 'bi-droplet',
			self.SECURITY: 'bi-shield-check',
			self.NOISE: 'bi-volume-up',
			self.OTHER: 'bi-question-circle',
		}.get(self.category, 'bi-chat-dots')


class ComplaintMedia(TimeStampedModel):
	"""
	Media files (images/videos) attached to complaints.
	Uploaded to Google Drive and URL stored here.
	"""
	MEDIA_TYPE_IMAGE = 'image'
	MEDIA_TYPE_VIDEO = 'video'
	MEDIA_TYPE_CHOICES = [
		(MEDIA_TYPE_IMAGE, 'Image'),
		(MEDIA_TYPE_VIDEO, 'Video'),
	]
	
	complaint = models.ForeignKey(
		Complaint,
		on_delete=models.CASCADE,
		related_name='media_files'
	)
	media_type = models.CharField(max_length=10, choices=MEDIA_TYPE_CHOICES, default=MEDIA_TYPE_IMAGE)
	file_url = models.URLField(max_length=500, help_text='Google Drive URL of the uploaded file')
	file_name = models.CharField(max_length=255, blank=True, help_text='Original filename')
	file_size = models.BigIntegerField(null=True, blank=True, help_text='File size in bytes')
	thumbnail_url = models.URLField(max_length=500, blank=True, help_text='Thumbnail URL for images/videos')
	
	class Meta:
		ordering = ['created_at']
		verbose_name = 'Complaint Media'
		verbose_name_plural = 'Complaint Media'
	
	def __str__(self):
		return f"{self.media_type} for complaint #{self.complaint_id}"
	
	def is_image(self):
		return self.media_type == self.MEDIA_TYPE_IMAGE
	
	def is_video(self):
		return self.media_type == self.MEDIA_TYPE_VIDEO


class ComplaintComment(TimeStampedModel):
	"""
	Comments/updates on complaints by PG admins
	"""
	complaint = models.ForeignKey(
		Complaint, 
		on_delete=models.CASCADE, 
		related_name='comments'
	)
	user = models.ForeignKey(
		get_user_model(), 
		on_delete=models.CASCADE,
		help_text='Admin who added the comment'
	)
	comment = models.TextField(help_text='Comment or update on the complaint')
	is_internal = models.BooleanField(
		default=False, 
		help_text='Internal notes (not visible to tenant)'
	)
	
	class Meta:
		ordering = ['created_at']
	
	def __str__(self):
		return f"Comment on {self.complaint.title} by {self.user.get_full_name() or self.user.email}"


class OldTenant(TimeStampedModel):
	"""
	Archive of tenant data from deleted bookings.
	Stores key personal information before booking deletion for historical records.
	"""
	pg = models.ForeignKey(
		PG, 
		on_delete=models.CASCADE, 
		related_name='old_tenants',
		help_text='PG where the tenant resided'
	)
	
	# Personal details
	full_name = models.CharField(max_length=255, help_text='Full name of the tenant')
	father_name = models.CharField(max_length=255, blank=True, help_text="Father's name")
	mother_name = models.CharField(max_length=255, blank=True, help_text="Mother's name")
	
	# Contact details
	email = models.EmailField(help_text='Email address')
	phone = models.CharField(max_length=20, blank=True, help_text='Phone number')
	whatsapp_number = models.CharField(max_length=20, blank=True, help_text='WhatsApp number')
	
	# Address
	address = models.TextField(blank=True, help_text='Permanent address')
	
	# Stay details
	room_no = models.CharField(max_length=20, blank=True, help_text='Room number during stay')
	bed_no = models.PositiveSmallIntegerField(null=True, blank=True, help_text='Bed/share number during stay')
	joining_date = models.DateField(null=True, blank=True, help_text='Date when tenant joined')
	leaving_date = models.DateField(null=True, blank=True, help_text='Date when tenant left')
	leaving_reason = models.TextField(blank=True, help_text='Reason for leaving')
	
	# Financial info
	advance_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Advance amount paid')
	advance_returned = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Advance amount returned')
	
	# Reference to original user (nullable - user might be deleted)
	original_user = models.ForeignKey(
		get_user_model(),
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='old_tenant_records',
		help_text='Original user account (if still exists)'
	)
	
	# Original booking ID for reference
	original_booking_id = models.IntegerField(null=True, blank=True, help_text='Original booking ID before deletion')
	
	# Archive metadata
	archived_at = models.DateTimeField(auto_now_add=True, help_text='When this record was archived')
	archived_by = models.ForeignKey(
		get_user_model(),
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='archived_tenants',
		help_text='Admin who archived/deleted the booking'
	)
	
	class Meta:
		ordering = ['-archived_at']
		indexes = [
			models.Index(fields=['pg', 'archived_at']),
			models.Index(fields=['joining_date']),
			models.Index(fields=['leaving_date']),
			models.Index(fields=['full_name']),
		]
	
	def __str__(self):
		return f"{self.full_name} - {self.pg.name} ({self.leaving_date})"
	
	@property
	def stay_duration_days(self):
		"""Calculate the duration of stay in days"""
		if self.joining_date and self.leaving_date:
			return (self.leaving_date - self.joining_date).days
		return None
