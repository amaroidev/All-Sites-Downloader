import requests
import json

# Test video_info endpoint
url = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'  # Rickroll video
try:
    response = requests.post('http://localhost:5000/api/video_info',
                            json={'url': url},
                            headers={'Content-Type': 'application/json'})
    print('Status:', response.status_code)
    print('Response:', response.text[:500])
    if response.status_code == 200:
        data = response.json()
        print('Success! Keys:', list(data.keys()))
    else:
        print('Error!')
except Exception as e:
    print('Exception:', e)