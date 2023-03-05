import discord

client = discord.Client()
token = 'MTA4MDE4MDM4ODY0NDc5ODY1NQ.GjpOA3.WiVcYFlOg0hJ4dGZ2jRj_UYj6ujavqRcnkAh_8'

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')

@client.event
async def on_message(message):
    if message.content == 'redo of healer':
        await message.channel.send('Is good!')

client.run(token)
