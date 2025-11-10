// frontend/js/config.js.example
// RENAME THIS FILE to config.js and add your real values.
// DO NOT COMMIT your real config.js file.

window._workshopConfig = {
  cognito: {
    // Your User Pool ID from Cognito
    userPoolId: 'YOUR_USER_POOL_ID',
    
    // Your Client ID from Cognito
    userPoolClientId: 'YOUR_CLIENT_ID',
    
    // Your Region (e.g., us-east-1)
    region: 'YOUR_AWS_REGION'
  },
  api: {
    // Your API Gateway URL (from the '$default' stage)
    invokeUrl: 'YOUR_API_GATEWAY_INVOKE_URL'
  }
};