# Use the AWS Lambda Python 3.12 base image
FROM public.ecr.aws/lambda/python:3.12

# Install system dependencies for Playwright/Chromium
RUN dnf install -y \
    atk \
    cups-libs \
    gtk3 \
    libXcomposite \
    libXcursor \
    libXdamage \
    libXext \
    libXi \
    libXrandr \
    libXScrnSaver \
    libXtst \
    pango \
    at-spi2-atk \
    libX11 \
    libxshmfence \
    mesa-libgbm \
    alsa-lib \
    nss \
    nspr \
    expat \
    libXau \
    libxcb \
    libXrender \
    libdrm \
    libxkbcommon \
    at-spi2-core

# Set Playwright browser path
ENV PLAYWRIGHT_BROWSERS_PATH=/var/task/pw-browsers

# Copy requirements and install
COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install -r requirements.txt

# Install Playwright browsers
RUN playwright install chromium

# Copy all code
COPY . ${LAMBDA_TASK_ROOT}

# Set the CMD to your handler
CMD [ "cloud/lambda_crawler.handler" ]
