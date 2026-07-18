from setuptools import setup, find_packages

setup(
    name='local-ai-genealogy-tool',
    version='0.1.0',
    author='Your Name',
    author_email='your.email@example.com',
    description='A local AI tool for genealogy research and family tree management.',
    packages=find_packages(),
    install_requires=[
        'langchain',
        'langchain-community',
        'langchain-ollama',
        'ollama',
        'httpx',
        'google-genai',
        'sqlalchemy',
    ],
    entry_points={
        'console_scripts': [
            'genealogy-bootstrap=tools.bootstrap:main',
        ],
    },
)
