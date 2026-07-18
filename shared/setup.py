from setuptools import setup, find_packages

setup(
    name="agentmesh-shared",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "redis",
        "requests",
        "psycopg2-binary>=2.9.0",
        "PyJWT",
        "pydantic>=2.9.0",
        "Pillow",
    ],
)
