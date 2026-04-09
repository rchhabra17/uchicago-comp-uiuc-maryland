import asyncio
from bot import MyXchangeClient

async def main():
    client = MyXchangeClient("34.197.188.76:3333", "maryland_uiuc", "torch-karma-beacon")
    
    async def probe():
        await asyncio.sleep(5)
        print("------------- TRAP STATE --------------")
        for oid, info in client.open_orders.items():
            if info[0].symbol in client.b_relevant:
                print(f"Open Order: {info[0].symbol} at {info[0].limit.px}")
        import sys
        sys.exit(0)
        
    asyncio.create_task(probe())
    await client.start()

asyncio.run(main())
