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
	# Notice period in days (default 30 days)
	notice_period = models.PositiveIntegerField(default=30, help_text="Notice period in days required before leaving.")
	# Day-wise booking fee per day
	daywise_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Fee per day for day-wise/short-term bookings.")

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
