document.addEventListener('DOMContentLoaded', function() {
    // Initialize tooltips
    var tooltipList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'))
        .map(el => new bootstrap.Tooltip(el));

    // Fix browser back button navigation
    window.addEventListener('pageshow', e => {
        if (e.persisted || window.performance?.navigation.type === 2) hideLoading();
    });
    
    // Loading overlay functions
    window.showLoading = function(message) {
        const overlay = document.getElementById('loadingOverlay');
        if (overlay) {
            if (message) {
                const messageEl = overlay.querySelector('.spinner-container p');
                if (messageEl) messageEl.textContent = message;
            }
            overlay.classList.add('active');
            document.body.style.overflow = 'hidden';
            window.loadingTimeout = setTimeout(hideLoading, 30000); // Safety timeout
        }
    };

    window.hideLoading = function() {
        const overlay = document.getElementById('loadingOverlay');
        if (overlay) {
            overlay.classList.remove('active');
            document.body.style.overflow = '';
            if (window.loadingTimeout) {
                clearTimeout(window.loadingTimeout);
                window.loadingTimeout = null;
            }
        }
    };
    
    // Reset loading when page becomes visible
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') hideLoading();
    });

    // Add loading state to buttons and forms
    document.querySelectorAll('button[type="submit"]').forEach(button => {
        const form = button.closest('form');
        if (form && !form.classList.contains('no-loading')) {
            button.addEventListener('click', function() {
                this.classList.add('btn-loading');
                if (!form.getAttribute('data-no-loading-overlay')) {
                    showLoading('Processing your request...');
                }
            });
        }
    });
    
    // Add loading to form submissions
    document.querySelectorAll('form:not(.no-loading)').forEach(form => {
        form.addEventListener('submit', () => showLoading());
    });
    
    document.querySelectorAll('.view-petition, a[href^="/petition/"]').forEach(link => {
        link.addEventListener('click', () => showLoading('Loading petition details...'));
    });

    // Check for notifications
    function checkNotifications() {
        const badge = document.getElementById('notification-badge');
        const notifLink = document.getElementById('notificationLink');
        if (!badge || !notifLink) return;
        
        fetch('/api/notifications/count')
            .then(response => response.json())
            .then(data => {
                const count = data.count || 0;
                if (count > 0) {
                    badge.textContent = count;
                    badge.classList.remove('d-none');
                    notifLink.querySelector('i')?.classList.add('notification-active');
                } else {
                    badge.classList.add('d-none');
                    notifLink.querySelector('i')?.classList.remove('notification-active');
                }
            })
            .catch(console.error);
    }
    
    // Check notifications initially and when tab becomes visible
    checkNotifications();
    document.addEventListener('visibilitychange', function() {
        if (document.visibilityState === 'visible') checkNotifications();
    });
    
    // Set up automated notification checking
    setInterval(checkNotifications, 120000);

    // Initialize charts
    // Petition analytics chart
    const petitionChartEl = document.getElementById('petitionChart');
    if (petitionChartEl && typeof Chart !== 'undefined' && typeof chartData !== 'undefined') {
        const petitionChart = new Chart(petitionChartEl, {
            type: 'line',
            data: {
                labels: chartData.map(item => item.month),
                datasets: [{
                    label: 'Petitions',
                    data: chartData.map(item => item.count),
                    backgroundColor: 'rgba(58, 134, 255, 0.2)',
                    borderColor: 'rgba(58, 134, 255, 1)',
                    borderWidth: 2,
                    tension: 0.3,
                    pointRadius: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: { precision: 0 }
                    }
                }
            }
        });
        
        // Toggle chart type between line/bar
        document.querySelectorAll('[data-chart-type]').forEach(button => {
            button.addEventListener('click', function() {
                const chartType = this.getAttribute('data-chart-type');
                document.querySelectorAll('[data-chart-type]').forEach(btn => btn.classList.remove('active'));
                this.classList.add('active');
                
                petitionChart.config.type = chartType;
                petitionChart.data.datasets[0].tension = chartType === 'bar' ? 0 : 0.3;
                petitionChart.update();
            });
        });
    }
    
    // Status distribution chart
    const statusChartEl = document.getElementById('statusChart');
    if (statusChartEl && typeof Chart !== 'undefined' && typeof statusData !== 'undefined') {
        new Chart(statusChartEl, {
            type: 'doughnut',
            data: {
                labels: statusData.map(item => item.name),
                datasets: [{
                    data: statusData.map(item => item.count),
                    backgroundColor: ['#ffbe0b', '#8338ec', '#3a86ff', '#fb8500', '#38b000', '#ff006e'],
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '60%',
                plugins: {
                    legend: {
                        position: 'right',
                        align: 'center'
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                const value = context.raw || 0;
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                const percentage = Math.round((value / total) * 100);
                                return `${context.label}: ${value} (${percentage}%)`;
                            }
                        }
                    }
                }
            }
        });
    }
    
    // Priority distribution chart
    const priorityChartEl = document.getElementById('priorityChart');
    if (priorityChartEl && typeof Chart !== 'undefined' && typeof priorityData !== 'undefined') {
        new Chart(priorityChartEl, {
            type: 'pie',
            data: {
                labels: priorityData.map(item => item.name),
                datasets: [{
                    data: priorityData.map(item => item.count),
                    backgroundColor: ['#ff006e', '#ffbe0b', '#3a86ff'],
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '60%',
                plugins: {
                    legend: {
                        position: 'right',
                        align: 'center'
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                const value = context.raw || 0;
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                const percentage = Math.round((value / total) * 100);
                                return `${context.label}: ${value} (${percentage}%)`;
                            }
                        }
                    }
                }
            }
        });
    }
    
    // File upload preview
    document.querySelectorAll('input[type="file"]').forEach(input => {
        input.addEventListener('change', function() {
            const filePreview = document.getElementById('file-preview');
            if (filePreview && this.files?.length > 0) {
                const file = this.files[0];
                const fileSize = (file.size / 1024).toFixed(2);
                const ext = file.name.split('.').pop().toLowerCase();
                const iconClass = {
                    'pdf': 'fa-file-pdf', 'doc': 'fa-file-word', 'docx': 'fa-file-word',
                    'xls': 'fa-file-excel', 'xlsx': 'fa-file-excel', 'txt': 'fa-file-alt',
                    'jpg': 'fa-file-image', 'jpeg': 'fa-file-image', 'png': 'fa-file-image',
                    'gif': 'fa-file-image'
                }[ext] || 'fa-file';
                
                filePreview.innerHTML = `
                    <div class="alert alert-info d-flex align-items-center">
                        <i class="fas ${iconClass} fa-lg me-3"></i>
                        <div>
                            <strong>${file.name}</strong>
                            <small class="d-block text-muted">${fileSize} KB</small>
                        </div>
                    </div>
                `;
                
                // Add image preview
                if (file.type.match('image.*')) {
                    const reader = new FileReader();
                    reader.onload = e => {
                        filePreview.innerHTML += `
                            <div class="mt-2">
                                <img src="${e.target.result}" class="img-thumbnail" style="max-height: 200px;" />
                            </div>
                        `;
                    };
                    reader.readAsDataURL(file);
                }
                
                filePreview.classList.remove('d-none');
            } else if (filePreview) {
                filePreview.classList.add('d-none');
            }
        });
    });
    
    // Delete petition confirmation
    const deleteForm = document.getElementById('deletePetitionForm');
    if (deleteForm) {
        deleteForm.addEventListener('submit', function(e) {
            if (!confirm('Are you sure you want to delete this petition? This action cannot be undone.')) {
                e.preventDefault();
            }
        });
    }
    
    // Department reassignment modal
    const reassignBtn = document.getElementById('reassignDeptBtn');
    if (reassignBtn) {
        reassignBtn.addEventListener('click', () => {
            new bootstrap.Modal(document.getElementById('reassignDeptModal')).show();
        });
    }
    
    // Hide loading on page load complete
    window.addEventListener('load', hideLoading);
});