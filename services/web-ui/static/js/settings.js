// settings.js - Handles theme and font size settings with localStorage persistence

let currentTheme = 'light';
let currentFontScale = 1;

function loadSettings() {
    const savedTheme = localStorage.getItem('helga-theme');
    const savedFontScale = localStorage.getItem('helga-font-scale');

    if (savedTheme && ['light', 'dark'].includes(savedTheme)) {
        currentTheme = savedTheme;
    }
    if (savedFontScale) {
        currentFontScale = parseFloat(savedFontScale);
    }

    applySettings();
}

function saveSettings() {
    localStorage.setItem('helga-theme', currentTheme);
    localStorage.setItem('helga-font-scale', currentFontScale.toString());
}

function applySettings() {
    // Apply theme
    document.documentElement.setAttribute('data-theme', currentTheme);

    // Apply font scale
    document.documentElement.style.setProperty('--font-scale', currentFontScale);

    // Update UI
    updateThemeSelector();
    updateFontSizeDisplay();
}

function updateThemeSelector() {
    const options = document.querySelectorAll('.theme-option');
    options.forEach(option => {
        option.classList.toggle('selected', option.dataset.theme === currentTheme);
    });
}

function updateFontSizeDisplay() {
    const display = document.getElementById('font-size-display');
    if (display) {
        display.textContent = Math.round(currentFontScale * 100) + '%';
    }
}

function setTheme(theme) {
    currentTheme = theme;
    applySettings();
    saveSettings();
}

function adjustFontSize(delta) {
    currentFontScale = Math.max(0.5, Math.min(2, currentFontScale + delta));
    applySettings();
    saveSettings();
}

document.addEventListener('DOMContentLoaded', function () {
    loadSettings();

    // Settings modal
    const settingsBtn = document.getElementById('settings-btn');
    const settingsModal = document.getElementById('settings-modal');
    const settingsClose = document.getElementById('settings-close');

    if (settingsBtn && settingsModal) {
        settingsBtn.addEventListener('click', () => {
            settingsModal.classList.remove('hidden');
            // A11Y: Focus first interactive element
            const firstBtn = settingsModal.querySelector('button, [tabindex]');
            if (firstBtn) firstBtn.focus();
        });

        const closeModal = () => {
            settingsModal.classList.add('hidden');
            settingsBtn.focus(); // A11Y: restore focus
        };

        settingsClose.addEventListener('click', closeModal);

        // Close on outside click
        settingsModal.addEventListener('click', (e) => {
            if (e.target === settingsModal) closeModal();
        });

        // A11Y: Close on Escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !settingsModal.classList.contains('hidden')) {
                closeModal();
            }
        });

        // A11Y: Focus trap inside modal
        settingsModal.addEventListener('keydown', (e) => {
            if (e.key !== 'Tab') return;
            const focusable = settingsModal.querySelectorAll('button, [tabindex]:not([tabindex="-1"])');
            if (focusable.length === 0) return;
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        });
    }

    // Theme selector
    const themeOptions = document.querySelectorAll('.theme-option');
    themeOptions.forEach(option => {
        option.addEventListener('click', () => {
            setTheme(option.dataset.theme);
        });
    });

    // Font size controls
    const fontDecrease = document.getElementById('font-decrease');
    const fontIncrease = document.getElementById('font-increase');

    if (fontDecrease) {
        fontDecrease.addEventListener('click', () => adjustFontSize(-0.1));
    }
    if (fontIncrease) {
        fontIncrease.addEventListener('click', () => adjustFontSize(0.1));
    }
});