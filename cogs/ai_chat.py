import discord
from discord import app_commands
from discord.ext import commands
from google import genai  # 新しいSDK
import os

GEMINI_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_KEY:
    client = genai.Client(api_key=GEMINI_KEY)
    MODEL_ID = 'gemini-3-flash-preview'
else:
    client = None

class AIChatCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ask", description="AI(Gemini)に直接質問や相談をします")
    @app_commands.describe(prompt="AIへの質問内容")
    async def ask_ai(self, interaction: discord.Interaction, prompt: str):
        if not client:
            await interaction.response.send_message("❌ AI機能が無効化されています。(APIキー未設定)", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            # 新しいSDKでの生成
            response = client.models.generate_content(
                model=MODEL_ID,
                contents=prompt
            )
            text = response.text

            embed = discord.Embed(title="🤖 AI Answer", description=text[:4000], color=discord.Color.green())
            embed.set_footer(text=f"Q: {prompt}")
            
            await interaction.followup.send(embed=embed)

        except Exception as e:
            await interaction.followup.send(f"❌ エラーが発生しました: {e}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(AIChatCog(bot))