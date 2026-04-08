import requests

username = 'username'
token = 'your_personal_access_token'
api_url = f'https://api.github.com/users/{username}/gists'

def fetch_gists():
    try:
        response = requests.get(api_url, headers={'Authorization': f'token {token}'})
        response.raise_for_status()
        gists = response.json()
        
        print(f'Gists for user {username}:')
        for index, gist in enumerate(gists):
            print(f'Gist {index + 1}: {gist.get("description", "No description")}')
            print(f'URL: {gist["html_url"]}')
            print('Files:')
            for file in gist['files']:
                print(f'  - {file}')
            print('---')
    except requests.exceptions.RequestException as e:
        print(f'Error fetching gists: {e}')

fetch_gists()
