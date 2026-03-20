#!/bin/bash

# Exit the script if any command fails
set -e

# Define variables
ZIP_FILE="caddie_repo.zip"
LOCAL_DIR="caddie-repo"
REMOTE_REPO="https://github.com/kfreedisme42-tech/Tactical-Gamersguide-.git"

# Step 1: Unzip the repo
echo "Unzipping the repository..."
unzip -o $ZIP_FILE

# Step 2: Navigate to the directory
echo "Navigating to directory..."
cd $LOCAL_DIR

# Step 3: Initialize Git (if not already initialized)
if [ ! -d ".git" ]; then
    echo "Initializing git repository..."
    git init
fi

# Step 4: Add all files to staging
echo "Adding all files..."
git add .

# Step 5: Commit all files
echo "Committing files..."
git commit -m "Automated commit - Add all unzipped files"

# Step 6: Add remote repository
REMOTE_EXISTS=$(git remote)
if [ -z "$REMOTE_EXISTS" ]; then
    echo "Adding remote repository..."
    git remote add origin $REMOTE_REPO
else
    echo "Remote repository already exists."
fi

# Step 7: Push the code to the remote
echo "Pushing all changes to remote repository..."
git push --all -u origin main

# Cleanup message
echo "Process completed successfully!"