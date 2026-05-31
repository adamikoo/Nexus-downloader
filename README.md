<a name="top"></a>
<p align="center">
  <img src="logo.png" alt="Nexus Direct Downloader Logo" width="220px"/>
</p>

# Nexus Direct Downloader

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![JavaScript](https://img.shields.io/badge/Language-JavaScript%20%2F%20React-yellow?style=flat-square&logo=javascript&logoColor=white)](#)
[![OS](https://img.shields.io/badge/OS-Windows-0078D4?style=flat-square&logo=windows&logoColor=white)](#)
[![Library](https://img.shields.io/badge/Impersonation-curl__cffi%20(Chrome%20124)-ff69b4?style=flat-square)](#)
[![API](https://img.shields.io/badge/API-NexusMods%20v2%20GraphQL-red?style=flat-square)](#)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![Free](https://img.shields.io/badge/free_for_personal_use-brightgreen?style=flat-square)](#)

⭐ Star us on GitHub — your support motivates us a lot! 🙏😊

[![Share](https://img.shields.io/badge/share-000000?logo=x&logoColor=white)](https://x.com/intent/tweet?text=Check%20out%20this%20project%20on%20GitHub:%20https://github.com/adamikoo/Restaurace29%20%23NexusMods%20%23Modding%20%23Python%20%23Web)
[![Share](https://img.shields.io/badge/share-1877F2?logo=facebook&logoColor=white)](https://www.facebook.com/sharer/sharer.php?u=https://github.com/adamikoo/Restaurace29)
[![Share](https://img.shields.io/badge/share-0A66C2?logo=linkedin&logoColor=white)](https://www.linkedin.com/sharing/share-offsite/?url=https://github.com/adamikoo/Restaurace29)
[![Share](https://img.shields.io/badge/share-FF4500?logo=reddit&logoColor=white)](https://www.reddit.com/submit?title=Check%20out%20this%20project%20on%20GitHub:%20https://github.com/adamikoo/Restaurace29)

---

> [!CAUTION]
> ### ⚠️ LEGAL DISCLAIMER & TERMS OF SERVICE NOTICE
> This project is an **independent, open-source educational utility** and is **not** affiliated, associated, authorized, endorsed by, or in any way officially connected with Nexus Mods (Blackwood Technologies Ltd) or any of its subsidiaries.
>
> 1. **Terms of Service**: Nexus Mods' Terms of Service and API Guidelines prohibit the unauthorized scraping or artificial bypass of download restrictions (such as speed limits imposed on non-premium accounts). Using session cookie extraction to bypass premium limits may violate their Terms of Service and could result in account suspension, ban, or restriction by Nexus Mods.
> 2. **No Liability**: The authors, contributors, and maintainers of this repository assume **no responsibility or liability** for any consequences, account bans, legal actions, damages, or losses resulting from your use of this software. You use this software **entirely at your own risk**.
> 3. **Data Privacy**: Your API keys and session cookies are stored **strictly locally** in `gui_config.json` on your own machine. This application makes no external connections other than directly to official `api.nexusmods.com` and `nexusmods.com` endpoints.
> 4. **No Commercial Use**: This tool is provided solely for personal educational research. Do not sell, distribute commercially, or utilize this tool to offer commercial bypass services.

---

## Table of Contents
- [About](#-about)
- [Key Features](#-key-features)
- [Aesthetic Redesign](#-aesthetic-redesign)
- [System Requirements](#-system-requirements)
- [Installation Guide](#-installation-guide)
- [Configuration Settings](#-configuration-settings)
- [License](#-license)
- [Disclaimer & Credits](#-disclaimer--credits)

---

## 🚀 About

**Nexus Direct Downloader** is a high-performance local web panel designed to optimize and accelerate mod downloads from Nexus Mods. 

Normally, free accounts on Nexus Mods are subject to severe download speed capping (e.g. 1.5MB/s to 3MB/s) and are forced to open browser tabs to wait through a 5-second countdown timer for every mod. 

This utility leverages **advanced multithreaded chunk downloads** alongside local browser session cookie delegation to bypass these restrictions. By mimicking authentic browser-based Chrome TLS fingerprinted requests, it secures direct CDN linkages, enabling download speeds up to **10x to 20x faster** than normal free browser queries—fully automated, in parallel, and with zero manual clicks.

---

## ✨ Key Features

| Feature | Description | Implementation Details |
| :--- | :--- | :--- |
| **8x Concurrent Streams** | Splits files into 8 parallel ranges for maximum download speed | Utilizes HTTP `Range` headers to download chunks concurrently, then stitches them instantly in local memory. |
| **Cloudflare Bypass** | Passes Cloudflare TLS fingerprint checks | Leverages Chrome impersonation (`curl_cffi` / TLS Client) to prevent Cloudflare blocks on session requests. |
| **Instant Collection Parser** | Loads entire Nexus mod collections in milliseconds | Highly optimized v2 GraphQL request via `latestPublishedRevision` reducing overhead from 15-second timeouts to **0.48s**. |
| **Bulk Mod Downloader** | Trigger downloads for hundreds of mods in a single click | Queue systems inside React with a 150ms stagger interval to prevent connection collisions or rate-limits. |
| **Local Explorer Integration** | Seamless Windows Explorer controls | Integrates native Tkinter folder picker and launches local Windows directories from the UI dashboard. |
| **Secure Key Handling** | 100% Local data persistence | Keeps sensitive personal API keys and session cookies strictly in a local file (`gui_config.json`). |

---

## 🎨 Aesthetic Redesign

The dashboard is built using modern web layouts for a premium, clean experience:
- **Minimalist Light Style**: Built on warm-white backgrounds (`#f8fafc`), clean border highlights (`#e2e8f0`), and soft drop shadows (`shadow-soft`).
- **Brand Dark Red Accent**: Elegant dark brand red colors (`#990000`) matching the logo's red accent for buttons, status indicators, badges, and progress tracks.
- **Zero Gradients & Zero Emojis**: Crisp flat-vector outlines and clean SVGs replace distracting neon gradients and playful emojis, delivering an executive, high-tech structure.

---

## 💻 System Requirements

- **Operating System**: Windows 10 or 11
- **Python Runtime**: Python 3.8 to 3.12 (with SSL support)
- **External Dependencies**: `curl_cffi` (for Chrome impersonation support)

---

## 📝 Installation Guide

Follow these steps to run the downloader on your Windows machine:

```powershell
# 1. Clone the repository to your local computer
git clone https://github.com/adamikoo/Restaurace29.git

# 2. Navigate into the project folder
cd Restaurace29

# 3. Install required Python library dependencies
pip install curl-cffi

# 4. Launch the local web server utility
python run_gui.py
```

The script will automatically spin up a local server on port `8000` and open your default web browser to the dashboard panel:
👉 **Local Web Panel URL:** `http://localhost:8000`

---

## ⚙️ Configuration Settings

To unlock premium-level speeds and automatic file list loading on a free account, complete these configuration settings in the **API Settings** modal in the top-right corner of the dashboard:

### 1. Personal API Key (Required for Mod Metadata)
- Go to your official [Nexus Mods API Access Settings](https://www.nexusmods.com/users/myaccount?tab=api).
- Scroll down, generate a **Personal API Key**, and paste it into the UI setup.

### 2. Browser Session Cookie (Required for Speed Limit Bypass)
To enable the high-speed download bypass on a free account:
1. Log in to your account at [nexusmods.com](https://www.nexusmods.com/).
2. Press **F12** to open Developer Tools, go to the **Network** tab, and **Refresh** the page.
3. Click the very first network request in the list.
4. Under the **Headers** sub-tab, scroll down to **Request Headers**.
5. Find the **Cookie:** header (begins with `cf_clearance=...`), copy its **entire, long string value**, and paste it into the **Nexus Browser Cookie** field in the UI settings.
6. Click **Save Configuration**.

---

## 📃 License

This project is distributed under the [MIT License](LICENSE). 

You are free to use, modify, and distribute this software for personal and educational purposes, provided all legal disclaimers, credits, and copyright notices remain intact.

---

## 🗨️ Contacts & Feedback

For questions, troubleshooting, or code improvements, feel free to contribute to this repository:
- **GitHub Repository**: [adamikoo/Restaurace29](https://github.com/adamikoo/Restaurace29)
- **Issue Tracker**: Submit bugs or feature requests on our [GitHub Issues](https://github.com/adamikoo/Restaurace29/issues) page.

[Back to top](#top)
