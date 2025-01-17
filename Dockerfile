FROM python:3.9

# Set the working directory to /app
WORKDIR /app

# copy the requirements file used for dependencies
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --trusted-host pypi.python.org -r requirements.txt

# Copy the rest of the working directory contents into the container at /app
COPY app.py app.py
COPY utwint utwint
COPY utwee.py utwee.py

# Run app.py when the container launches
ENTRYPOINT ["python", "app.py"]
