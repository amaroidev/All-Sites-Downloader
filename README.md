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

## 🌐 Deployment

### Heroku Deployment
1. Create a new Heroku app
2. Connect your GitHub repository
3. Add the following buildpacks:
   - `heroku/python`
4. Set environment variables:
   - `FLASK_APP=app.py`
5. Deploy from GitHub

### Railway Deployment
1. Connect your GitHub repository to Railway
2. Railway will auto-detect the Python app
3. Deploy automatically

### DigitalOcean/VPS Deployment
```bash
# Install dependencies
sudo apt update
sudo apt install python3 python3-pip python3-venv nginx

# Clone repository
git clone <your-repo-url>
cd universal-video-downloader

# Setup application
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install gunicorn

# Run with Gunicorn
gunicorn --bind 0.0.0.0:5000 app:app
```

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

## 📋 Supported Platforms

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