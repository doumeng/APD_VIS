# Project Upload Instructions

This project is intended to be hosted on GitHub. However, Git was not found in the environment, so the upload process could not be automated.

## Prerequisites

1.  **Install Git**: Download and install Git from [git-scm.com](https://git-scm.com/downloads).
2.  **Install GitHub CLI (Optional but recommended)**: Download from [cli.github.com](https://cli.github.com/).

## Steps to Upload

### Option 1: Use Git (Recommended)

1.  Open a terminal in this directory: `D:\code\上位机\预处理板显示上位机\v3`
2.  Initialize the repository:
    ```bash
    git init
    ```
3.  Add files and commit:
    ```bash
    git add .
    git commit -m "Initial commit"
    ```
4.  Create a new repository on GitHub (if using GitHub CLI):
    ```bash
    gh repo create my-project-name --public --source=. --remote=origin
    ```
    Or manually create a repository on GitHub website and add remote:
    ```bash
    git remote add origin https://github.com/USERNAME/REPOSITORY.git
    ```
5.  Push the code:
    ```bash
    git push -u origin main
    ```

### Option 2: Download ZIP (Alternative)

If you cannot install Git, download the generated **project.zip** file from this directory and upload it manually to GitHub via the web interface (drag and drop or "Upload files").
