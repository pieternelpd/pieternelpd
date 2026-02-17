// === State ===
let allEvents = [];
let filteredEvents = [];
let userLocation = null; // { lat, lng }
let map, markerClusterGroup, userMarker;

const activeFilters = {
    disciplines: new Set(),
    sources: new Set(),
    states: new Set(),
    maxDistanceKm: Infinity,
    dateFrom: null,
    dateTo: null,
};

// === Discipline colors for markers ===
const DISCIPLINE_COLORS = {
    road: '#4f8cff',
    criterium: '#f59e0b',
    track: '#a855f7',
    mtb: '#22c55e',
    gravel: '#d97706',
    bmx: '#ef4444',
    cyclocross: '#14b8a6',
};

// === Init ===
document.addEventListener('DOMContentLoaded', async () => {
    initMap();
    await loadEvents();
    buildFilterChips();
    bindControls();
    applyFilters();
});

// === Map ===
function initMap() {
    map = L.map('map', {
        center: [-28.5, 134],
        zoom: 5,
        zoomControl: true,
        attributionControl: true,
    });

    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
        maxZoom: 19,
    }).addTo(map);

    markerClusterGroup = L.markerClusterGroup({
        maxClusterRadius: 40,
        spiderfyOnMaxZoom: true,
        showCoverageOnHover: false,
    });
    map.addLayer(markerClusterGroup);
}

// === Load events ===
async function loadEvents() {
    try {
        const resp = await fetch('data/events.json');
        allEvents = await resp.json();
    } catch (e) {
        console.error('Failed to load events:', e);
        allEvents = [];
    }
}

// === Build filter chips ===
function buildFilterChips() {
    const disciplines = [...new Set(allEvents.map(e => e.discipline))].sort();
    const sources = [...new Set(allEvents.map(e => e.source))].sort();
    const states = [...new Set(allEvents.map(e => e.state).filter(Boolean))].sort();

    const SOURCE_LABELS = {
        auscycling: 'AusCycling',
        wcmcc: 'West Coast Masters',
    };

    renderChips('discipline-filters', disciplines, activeFilters.disciplines, d => d);
    renderChips('source-filters', sources, activeFilters.sources, s => SOURCE_LABELS[s] || s);
    renderChips('state-filters', states, activeFilters.states, s => s);
}

function renderChips(containerId, values, activeSet, labelFn) {
    const container = document.getElementById(containerId);
    container.innerHTML = '';

    // "All" chip
    const allChip = document.createElement('button');
    allChip.className = 'chip' + (activeSet.size === 0 ? ' active' : '');
    allChip.textContent = 'All';
    allChip.addEventListener('click', () => {
        activeSet.clear();
        updateChipStates(container, activeSet);
        applyFilters();
    });
    container.appendChild(allChip);

    values.forEach(val => {
        const chip = document.createElement('button');
        chip.className = 'chip' + (activeSet.has(val) ? ' active' : '');
        chip.textContent = labelFn(val);
        chip.dataset.value = val;
        chip.addEventListener('click', () => {
            if (activeSet.has(val)) {
                activeSet.delete(val);
            } else {
                activeSet.add(val);
            }
            updateChipStates(container, activeSet);
            applyFilters();
        });
        container.appendChild(chip);
    });
}

function updateChipStates(container, activeSet) {
    const chips = container.querySelectorAll('.chip');
    chips.forEach(chip => {
        if (!chip.dataset.value) {
            // "All" chip
            chip.classList.toggle('active', activeSet.size === 0);
        } else {
            chip.classList.toggle('active', activeSet.has(chip.dataset.value));
        }
    });
}

// === Controls ===
function bindControls() {
    // Distance slider
    const slider = document.getElementById('distance-slider');
    const label = document.getElementById('distance-label');
    slider.addEventListener('input', () => {
        const val = parseInt(slider.value);
        if (val >= 2000) {
            activeFilters.maxDistanceKm = Infinity;
            label.textContent = 'Any distance';
        } else {
            activeFilters.maxDistanceKm = val;
            label.textContent = `${val} km`;
        }
        applyFilters();
    });

    // Locate button
    document.getElementById('locate-btn').addEventListener('click', geolocate);

    // Date range
    document.getElementById('date-from').addEventListener('change', (e) => {
        activeFilters.dateFrom = e.target.value || null;
        applyFilters();
    });
    document.getElementById('date-to').addEventListener('change', (e) => {
        activeFilters.dateTo = e.target.value || null;
        applyFilters();
    });

    // Sort
    document.getElementById('sort-select').addEventListener('change', () => {
        renderEventList();
    });
}

// === Geolocation ===
function geolocate() {
    if (!navigator.geolocation) {
        alert('Geolocation is not supported by your browser.');
        return;
    }

    const btn = document.getElementById('locate-btn');
    btn.textContent = 'Locating...';
    btn.disabled = true;

    navigator.geolocation.getCurrentPosition(
        (pos) => {
            userLocation = { lat: pos.coords.latitude, lng: pos.coords.longitude };
            btn.textContent = 'Location set';
            btn.disabled = false;

            // Add/move user marker
            if (userMarker) {
                userMarker.setLatLng([userLocation.lat, userLocation.lng]);
            } else {
                const icon = L.divIcon({ className: 'user-location-marker', iconSize: [16, 16] });
                userMarker = L.marker([userLocation.lat, userLocation.lng], { icon, zIndexOffset: 1000 })
                    .addTo(map)
                    .bindPopup('You are here');
            }

            // Enable distance sorting
            const sortSelect = document.getElementById('sort-select');
            if (!sortSelect.querySelector('[value="distance"]')) {
                // Already exists
            }

            applyFilters();
        },
        (err) => {
            btn.textContent = 'Use my location';
            btn.disabled = false;
            alert('Unable to get your location: ' + err.message);
        },
        { enableHighAccuracy: true, timeout: 10000 }
    );
}

// === Filtering ===
function applyFilters() {
    filteredEvents = allEvents.filter(event => {
        // Discipline filter
        if (activeFilters.disciplines.size > 0 && !activeFilters.disciplines.has(event.discipline)) {
            return false;
        }

        // Source filter
        if (activeFilters.sources.size > 0 && !activeFilters.sources.has(event.source)) {
            return false;
        }

        // State filter
        if (activeFilters.states.size > 0 && !activeFilters.states.has(event.state)) {
            return false;
        }

        // Date filter
        if (activeFilters.dateFrom && event.date < activeFilters.dateFrom) return false;
        if (activeFilters.dateTo && event.date > activeFilters.dateTo) return false;

        // Distance filter
        if (activeFilters.maxDistanceKm !== Infinity && userLocation && event.lat && event.lng) {
            const dist = haversine(userLocation.lat, userLocation.lng, event.lat, event.lng);
            event._distance = dist;
            if (dist > activeFilters.maxDistanceKm) return false;
        } else if (userLocation && event.lat && event.lng) {
            event._distance = haversine(userLocation.lat, userLocation.lng, event.lat, event.lng);
        } else {
            event._distance = null;
        }

        return true;
    });

    document.getElementById('event-count').textContent = filteredEvents.length;
    renderMarkers();
    renderEventList();
}

// === Render markers ===
function renderMarkers() {
    markerClusterGroup.clearLayers();

    filteredEvents.forEach(event => {
        if (!event.lat || !event.lng) return;

        const color = DISCIPLINE_COLORS[event.discipline] || '#4f8cff';
        const icon = L.divIcon({
            className: 'custom-marker',
            html: `<svg width="24" height="34" viewBox="0 0 24 34">
                <path d="M12 0C5.4 0 0 5.4 0 12c0 9 12 22 12 22s12-13 12-22C24 5.4 18.6 0 12 0z" fill="${color}"/>
                <circle cx="12" cy="12" r="5" fill="#fff" opacity="0.9"/>
            </svg>`,
            iconSize: [24, 34],
            iconAnchor: [12, 34],
            popupAnchor: [0, -34],
        });

        const dateStr = formatDate(event.date);
        const endStr = event.end_date ? ` - ${formatDate(event.end_date)}` : '';
        const distStr = event._distance != null ? `<br>${Math.round(event._distance)} km from you` : '';

        const marker = L.marker([event.lat, event.lng], { icon })
            .bindPopup(`
                <div class="popup-title">${escapeHtml(event.name)}</div>
                <div class="popup-meta">
                    <span class="discipline-badge ${event.discipline}">${event.discipline}</span><br>
                    ${dateStr}${endStr}<br>
                    ${escapeHtml(event.venue)}<br>
                    ${escapeHtml(event.organiser)}
                    ${distStr}
                </div>
                ${event.url ? `<a href="${event.url}" target="_blank" rel="noopener" style="color:var(--accent);font-size:0.8rem;">More info &rarr;</a>` : ''}
            `);

        marker._eventData = event;
        markerClusterGroup.addLayer(marker);
    });
}

// === Render event list ===
function renderEventList() {
    const container = document.getElementById('event-list-items');
    const sortBy = document.getElementById('sort-select').value;

    const sorted = [...filteredEvents].sort((a, b) => {
        if (sortBy === 'date') return (a.date || '').localeCompare(b.date || '');
        if (sortBy === 'distance') {
            if (a._distance == null && b._distance == null) return 0;
            if (a._distance == null) return 1;
            if (b._distance == null) return -1;
            return a._distance - b._distance;
        }
        if (sortBy === 'name') return a.name.localeCompare(b.name);
        return 0;
    });

    container.innerHTML = sorted.map(event => {
        const dateStr = formatDate(event.date);
        const endStr = event.end_date ? ` - ${formatDate(event.end_date)}` : '';
        const distStr = event._distance != null ? `<span class="event-distance">${Math.round(event._distance)} km</span>` : '';

        return `
            <div class="event-card" data-lat="${event.lat}" data-lng="${event.lng}">
                <div class="event-name">${escapeHtml(event.name)}</div>
                <div class="event-meta">
                    <span class="discipline-badge ${event.discipline}">${event.discipline}</span>
                    <span>${dateStr}${endStr}</span>
                    <span>${escapeHtml(event.venue)}</span>
                    ${event.state ? `<span>${event.state}</span>` : ''}
                    ${distStr}
                </div>
                ${event.description ? `<div class="event-description">${escapeHtml(event.description)}</div>` : ''}
            </div>
        `;
    }).join('');

    // Click to pan map
    container.querySelectorAll('.event-card').forEach(card => {
        card.addEventListener('click', () => {
            const lat = parseFloat(card.dataset.lat);
            const lng = parseFloat(card.dataset.lng);
            if (!isNaN(lat) && !isNaN(lng)) {
                map.setView([lat, lng], 12);

                // Find and open the matching marker popup
                markerClusterGroup.eachLayer(layer => {
                    if (layer._eventData &&
                        layer._eventData.lat === lat &&
                        layer._eventData.lng === lng) {
                        // Ensure cluster is spiderfied
                        markerClusterGroup.zoomToShowLayer(layer, () => {
                            layer.openPopup();
                        });
                    }
                });

                // Highlight card
                container.querySelectorAll('.event-card').forEach(c => c.classList.remove('highlighted'));
                card.classList.add('highlighted');
            }
        });
    });
}

// === Utilities ===
function haversine(lat1, lon1, lat2, lon2) {
    const R = 6371;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a = Math.sin(dLat / 2) ** 2 +
              Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function toRad(deg) { return deg * Math.PI / 180; }

function formatDate(dateStr) {
    if (!dateStr) return '';
    try {
        const d = new Date(dateStr + 'T00:00:00');
        return d.toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
    } catch {
        return dateStr;
    }
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
