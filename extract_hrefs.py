import os
from lxml import html

# Function to extract hrefs based on XPath
def extract_hrefs_from_file(file_path, xpath):
    with open(file_path, 'r', encoding='utf-8') as file:
        file_content = file.read()
    
    tree = html.fromstring(file_content)
    elements = tree.xpath(xpath)
    
    hrefs = [element.get('href') for element in elements if element.get('href')]
    return hrefs

# Function to process all HTML files in a given directory
def process_html_files(directory, xpath):
    for filename in os.listdir(directory):
        if filename.endswith('.html'):
            file_path = os.path.join(directory, filename)
            hrefs = extract_hrefs_from_file(file_path, xpath)
            print(f'Hrefs in {filename}:')
            for href in hrefs:
                print(href)
            print('---')

# Directory containing HTML files (this repo's github_following_users next to this script)
html_directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'github_following_users')
xpath_expression = "//a[(@data-hovercard-type='user' or @data-hovercard-type='organization') and @class='d-inline-block no-underline mb-1']"
# Process the HTML files
process_html_files(html_directory, xpath_expression)
