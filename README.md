Step 1: Obtain API Keys
You will need to get a personal API token from both Canvas and Todoist.

For Canvas: go to your canvas account, go to calender and obtain an icalender link.

For Todoist: Go to your Todoist account settings, select the Integrations tab, and your API token will be listed there.

Step 2: Prepare the Code and Dependencies
Clone the repository to your local machine.

Create a virtual environment and install the required Python packages by running pip install -r requirements.txt.

Step 3: Deploy to AWS Lambda
Create a ZIP file that includes the project's source code, the configuration file, and all the installed Python dependencies from your virtual environment.

In the AWS Management Console, create a new Lambda function. Select Python as the runtime.

Upload the ZIP file you created.

You will need to configure environment variables in your Lambda function to securely store your API keys. Do not hardcode them into the script.

Create the environment variables as follows: 
TODOIST_API_TOKEN,
TODOIST_PROJECT_ID
ICAL_URL

Set up a trigger for the Lambda function. This can be a CloudWatch Events or EventBridge trigger to run the sync script on a schedule (e.g., daily).

For more detailed information on setting up a Lambda function, you can refer to the official AWS documentation.

Integrating AWS Lambda with Your Favorite Tools
