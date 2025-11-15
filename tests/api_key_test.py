import os
import yaml

print("=== Checking API Configuration ===\n")

# Check if file exists
config_path = "configs/api_config.yaml"
if os.path.exists(config_path):
    print(f"✓ File found: {config_path}")
    
    # Try to load it
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        print("\n--- Config Contents ---")
        print(f"OpenRouter API Key: {config['openrouter']['api_key'][:20]}..." if config['openrouter']['api_key'] else "❌ API Key is EMPTY!")
        print(f"Base URL: {config['openrouter']['base_url']}")
        print(f"Model: {config['openrouter']['model']}")
        
        if not config['openrouter']['api_key'] or config['openrouter']['api_key'] == "":
            print("\n❌ ERROR: API key is empty!")
            print("Please add your OpenRouter API key to configs/api_config.yaml")
            print("\nSteps:")
            print("1. Go to https://openrouter.ai/")
            print("2. Sign up and get an API key")
            print("3. Edit configs/api_config.yaml")
            print("4. Replace the empty api_key with your key")
        else:
            print("\n✓ API key is configured!")
            
    except Exception as e:
        print(f"❌ Error reading config: {e}")
else:
    print(f"❌ File NOT found: {config_path}")
    print("\nCreating template file...")
    
    # Create the directory if it doesn't exist
    os.makedirs("configs", exist_ok=True)
    
    # Create template config
    template = """# API Configuration for KawaiiKombatant

openrouter:
  api_key: ""  # PUT YOUR OPENROUTER API KEY HERE!
  base_url: "https://openrouter.ai/api/v1"
  model: "google/gemma-3-27b-it:free"

settings:
  max_tokens: 1000
  temperature: 0.8
  context_window: 15
"""
    
    with open(config_path, 'w') as f:
        f.write(template)
    
    print(f"✓ Created template: {config_path}")
    print("\nNow edit this file and add your API key!")
    print("\nGet your key from: https://openrouter.ai/keys")