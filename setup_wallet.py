"""
One-time setup script — guides you through wallet creation and API credential generation.

Run this ONCE before starting the agent:
    python setup_wallet.py

It will:
1. Check if .env exists
2. Help you generate Polymarket API credentials from your wallet
3. Verify the credentials work
"""

import os
import re
import sys

def main():
    print("=" * 60)
    print("  Polymarket Trading Agent — Wallet Setup")
    print("=" * 60)
    print()

    # Step 1: Check for .env file
    if not os.path.exists(".env"):
        print("[!] No .env file found.")
        print("    Copy the template first:")
        print("    cp .env.example .env")
        print("    Then fill in your POLYGON_PRIVATE_KEY and run this again.")
        sys.exit(1)

    # Load environment
    from dotenv import load_dotenv
    load_dotenv()

    private_key = os.getenv("POLYGON_PRIVATE_KEY", "")

    # Step 2: Check for private key
    if not private_key or private_key == "0x_your_private_key_here":
        print("[!] POLYGON_PRIVATE_KEY is not set in .env")
        print()
        print("    You need a Polygon wallet. Here's how:")
        print()
        print("    OPTION A — Generate a new wallet (recommended for bots):")
        print("    $ python -c \"from eth_account import Account; a = Account.create(); print(f'Address: {a.address}'); print(f'Private Key: {a.key.hex()}')\"")
        print()
        print("    OPTION B — Export from MetaMask:")
        print("    MetaMask → Account → Export Private Key")
        print()
        print("    IMPORTANT SECURITY RULES:")
        print("    - Use a DEDICATED wallet for this bot, not your personal one")
        print("    - Never share your private key with anyone")
        print("    - The private key gives FULL control over the wallet")
        print()
        print("    After you have a private key, paste it into .env:")
        print("    POLYGON_PRIVATE_KEY=0xYourKeyHere")
        sys.exit(1)

    print("[+] Private key found in .env")
    print()

    # Step 3: Derive Polymarket API credentials
    print("[*] Deriving Polymarket API credentials from your wallet...")
    print("    This signs a message with your private key to prove ownership.")
    print()

    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.constants import POLYGON

        client = ClobClient(
            "https://clob.polymarket.com",
            key=private_key,
            chain_id=POLYGON,
            signature_type=0,
        )

        creds = client.create_or_derive_api_creds()
        print("[+] API credentials derived successfully!")
        print()
        print(f"    API Key:        {creds.api_key[:8]}...{creds.api_key[-4:]}")
        print(f"    API Secret:     {creds.api_secret[:4]}...{creds.api_secret[-4:]}")
        print(f"    API Passphrase: {creds.api_passphrase[:4]}...{creds.api_passphrase[-4:]}")
        print()

        # Step 4: Save to .env
        save = input("    Save these to .env? (y/n): ").strip().lower()
        if save == "y":
            with open(".env", "r") as f:
                env_content = f.read()

            # Use regex to safely replace existing values (handles re-runs)
            env_content = re.sub(
                r'^POLYMARKET_API_KEY=.*$',
                f'POLYMARKET_API_KEY={creds.api_key}',
                env_content, flags=re.MULTILINE
            )
            env_content = re.sub(
                r'^POLYMARKET_API_SECRET=.*$',
                f'POLYMARKET_API_SECRET={creds.api_secret}',
                env_content, flags=re.MULTILINE
            )
            env_content = re.sub(
                r'^POLYMARKET_API_PASSPHRASE=.*$',
                f'POLYMARKET_API_PASSPHRASE={creds.api_passphrase}',
                env_content, flags=re.MULTILINE
            )

            with open(".env", "w") as f:
                f.write(env_content)

            print("    [+] Saved to .env")
        else:
            print("    [ ] Not saved. Copy them manually into .env")

    except ImportError:
        print("[!] py-clob-client not installed. Run:")
        print("    pip install py-clob-client")
        sys.exit(1)
    except Exception as e:
        print(f"[!] Failed to derive credentials: {e}")
        print("    Make sure your private key is correct and you have internet access.")
        sys.exit(1)

    # Step 5: Funding reminder
    print()
    print("=" * 60)
    print("  NEXT STEPS — Fund Your Wallet")
    print("=" * 60)
    print()
    print("  1. Send MATIC to your wallet for gas fees (~$2 worth)")
    print("     Network: Polygon Mainnet (chain ID 137)")
    print()
    print("  2. Send USDC.e to your wallet for trading")
    print("     Start with $100 or less for testing")
    print("     Token: USDC.e on Polygon (0x2791bca1f2de4661ed88a30c99a7a9449aa84174)")
    print()
    print("  3. Deposit USDC.e into Polymarket")
    print("     Go to polymarket.com → Portfolio → Deposit")
    print("     Or the bot will handle approvals automatically")
    print()
    print("  4. Get your other API keys:")
    print("     - Anthropic (Claude): console.anthropic.com")
    print("     - NewsAPI: newsapi.org")
    print("     - Reddit: reddit.com/prefs/apps → Create App → Script type")
    print()
    print("  5. Run the agent in paper trading mode first:")
    print("     python main.py")
    print()
    print("  Setup complete!")


if __name__ == "__main__":
    main()
