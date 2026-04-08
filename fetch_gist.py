import requests

# Replace 'username' with the GitHub username you want to retrieve gists for
username = 'zoogie'

# GitHub API endpoint to retrieve user gists
api_url = f'https://api.github.com/users/{username}/gists'

def fetch_gists():
    try:
        response = requests.get(api_url)
        response.raise_for_status()  # Raise an error for bad status codes
        gists = response.json()
        
        # Log the retrieved gists
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

# Call the function
fetch_gists()
