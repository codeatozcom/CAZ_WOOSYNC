from setuptools import setup, find_packages

with open("requirements.txt") as f:
    install_requires = [line.strip() for line in f if line.strip() and not line.startswith("#")]

setup(
    name="caz_woosync",
    version="0.1.0",
    description="CAZ WooSync — WooCommerce sync for ERPNext",
    author="CodeAtoZ",
    author_email="support@codeatoz.com",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=install_requires,
)
