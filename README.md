# Universal Video Downloader

A powerful, modern web application for downloading videos from YouTube, Instagram, Twitter, TikTok, and 1000+ other platforms.

## 🚀 Features

- **Universal Support**: Download from 1000+ websites including:
  - YouTube, Instagram, Twitter, TikTok
  - Facebook, Vimeo, Twitch, Reddit
  - SoundCloud, Bandcamp, Dailymotion
  - And many more!

- **Multiple Formats**: 
  - Video downloads in best available quality
  - Audio-only downloads (MP3)
  - Various quality options

- **Real-time Progress**: 
  - Live download progress tracking
  - Speed and ETA monitoring
  - Download history

- **Modern UI**: 
  - Responsive glass-morphism design
  - Mobile-friendly interface
  - Dark theme optimized

- **Video Information**: 
  - Preview video details before downloading
  - Thumbnail, title, duration, views
  - Multiple format options

- **Revamped Architecture**: 
  - Improved download management with threading
  - Better error handling and retry logic
  - Session-based user tracking
  - Configurable rate limiting
  - Automatic cleanup of old downloads

## 🛠️ Installation

### Quick Start (Windows)
1. Double-click `setup.bat`
2. The script will automatically install everything and start the app

### Manual Installation
1. Clone or download this repository
2. Install Python 3.8+ if not already installed
3. Run the setup:

```bash
# Linux/Mac
chmod +x setup.sh
./setup.sh

# Windows
setup.bat
```

### Manual Setup
```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the application
python app.py
```

## 📱 Usage

1. Open your browser and go to `http://localhost:5000`
2. Paste any video URL from supported platforms
3. Click "Get Info" to preview video details (optional)
4. Select download type (Video or Audio)
5. Click "Download" and wait for completion
6. Download your file when ready

git clone <your-repo-url>
gunicorn --bind 0.0.0.0:5000 app:app
## 🌐 Deployment

### Recommended: Docker (Render, Railway, Fly.io, VPS)

1. Build the container locally and verify it runs:
  ```bash
  docker build -t all-sites-downloader .
  docker run --rm -p 8000:8000 -e FLASK_SECRET_KEY=change-me -v $(pwd)/downloads:/app/downloads all-sites-downloader
  ```
2. Push the image to your registry of choice (GHCR, Docker Hub, Render private registry).
3. Deploy the image on your platform and set environment variables:
  - `FLASK_SECRET_KEY` – strong random value
  - `DOWNLOAD_FOLDER` – override if you mount a different volume
  - `MAX_DOWNLOADS`, `JOB_RETENTION_HOURS`, etc. (optional)
4. Make sure the volume that backs `/app/downloads` (inside the container) is persistent.

**Render example:**
- Create a new Web Service → “Deploy an existing image from a registry”.
- Point to the pushed image, set the start command to the default (`gunicorn app:app --bind 0.0.0.0:8000`).
- Add a persistent disk and mount it at `/app/downloads`.

**Railway/Fly.io example:**
- Connect the GitHub repo and enable Docker deployment.
- Add environment variables in the dashboard.
- Configure a volume for `/app/downloads` (Fly) or a shared volume (Railway).

### Running without Docker (classic VPS)

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip ffmpeg nginx

git clone https://github.com/amaroidev/All-Sites-Downloader.git
cd All-Sites-Downloader

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

export FLASK_SECRET_KEY="change-me"
export DOWNLOAD_FOLDER="/srv/all-sites-downloader"
python app.py  # or gunicorn --bind 0.0.0.0:5000 app:app
```

Reverse-proxy the process with Nginx or Caddy, add HTTPS, and run it under a process manager (Systemd, Supervisor, or pm2).

## 🔧 Configuration

### Environment Variables
- `FLASK_SECRET_KEY`: Set a secure secret key for production
- `DOWNLOAD_FOLDER`: Custom download folder path
- `MAX_DOWNLOADS`: Maximum concurrent downloads (default: 5)

### Production Settings
For production deployment, update these settings in `app.py`:
```python
app.config['SECRET_KEY'] = 'your-secure-secret-key'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
```

## � Release Automation

The repository ships with `.github/workflows/release.yml`, which is triggered on demand or whenever a tag that starts with `v` (for example `v1.0.0`) is pushed.

### Windows executable
- The workflow runs PyInstaller on `windows-latest` and produces `dist/AllSitesDownloader.exe`.
- The binary is zipped (`AllSitesDownloader-windows.zip`), uploaded as an artifact, and attached automatically to the GitHub Release created from the tag.
- To build locally you can run:
  ```powershell
  pyinstaller app.py --name AllSitesDownloader --noconfirm --onefile ^
    --add-data "templates;templates" ^
    --add-data "static;static" ^
    --add-data "browser-extension;browser-extension"
  ```

### Docker image QA
- The workflow also performs a `docker build` on Ubuntu to ensure the container image stays healthy.
- By default it does not push the image. If you want automated pushes, extend the workflow with registry login and set repository secrets for your token.

### (Optional) Android wrapper
For teams that want an APK, the quickest win is to wrap the hosted web app inside a WebView shell:
1. Deploy the backend somewhere reachable over HTTPS.
2. Use [Capacitor](https://capacitorjs.com/) or [Cordova](https://cordova.apache.org/) to scaffold a minimal container:
   ```bash
   npm init @capacitor/app android-shell
   cd android-shell
   npx cap init "Universal Downloader" "com.example.universaldl" --web-dir=www
   ```
3. Drop a single-page stub in `www/index.html` that loads `https://your-hosted-domain` inside an iframe/WebView.
4. Configure network permissions and build the APK via Android Studio or `npx cap open android`.
5. Sign the APK and attach it to the GitHub Release alongside the Windows zip.

> Note: Kivy/BeeWare can deliver a fully offline Android client, but that path requires a bespoke mobile UI and is substantially more involved than a WebView wrapper.

## �📋 Supported Platforms

This downloader supports over 1000 websites including:

### Video Platforms
- YouTube, YouTube Music
- Vimeo, Dailymotion
- Twitch (clips and streams)
- Facebook, Instagram
- Twitter, TikTok

### Social Media
- Reddit (v.redd.it)
- LinkedIn videos
- Pinterest videos
- Snapchat Spotlight

### Audio Platforms
- SoundCloud
- Bandcamp
- Mixcloud
- AudioBoom

### News & Educational
- BBC iPlayer
- CNN
- Coursera
- Khan Academy

And many more! The full list is available at [yt-dlp supported sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md).

## 🛡️ Legal Notice

This tool is for educational and personal use only. Please respect the terms of service of the platforms you're downloading from and applicable copyright laws. Users are responsible for ensuring their use complies with relevant laws and platform terms.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🆘 Troubleshooting

### Common Issues

**Error: "No module named 'yt_dlp'"**
- Make sure you've activated the virtual environment
- Run `pip install -r requirements.txt`

**Downloads are slow**
- This depends on your internet connection and the source server
- Try downloading during off-peak hours

**Video not found/unavailable**
- Some videos may be region-locked or private
- Check if the URL is correct and publicly accessible

**Can't download from a specific site**
- Make sure yt-dlp supports the site
- Update yt-dlp to the latest version: `pip install --upgrade yt-dlp`

### Getting Help
- Check the [yt-dlp documentation](https://github.com/yt-dlp/yt-dlp)
- Open an issue on GitHub with details about your problem
- Include the error message and the URL you're trying to download

## 🔄 Updates

To update the application:
```bash
# Activate virtual environment
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate     # Windows

# Update yt-dlp
pip install --upgrade yt-dlp

# Update other dependencies
pip install --upgrade -r requirements.txt
```

## ⭐ Support

If you find this project helpful, please consider:
- Starring the repository
- Sharing it with others
- Contributing improvements
- Reporting bugs

---

Made with ❤️ for the community. Download responsibly! 🎬