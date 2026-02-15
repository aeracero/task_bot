import discord
from discord import app_commands
from discord.ext import commands
import google.generativeai as genai
import os

# メインのAPIキー設定を共有、またはここで再設定
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')

class AIChatCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ask", description="AI(Gemini)に直接質問や相談をします")
    @app_commands.describe(prompt="AIへの質問内容")
    async def ask_ai(self, interaction: discord.Interaction, prompt: str):
        # APIキーがない場合
        if not GEMINI_KEY:
            await interaction.response.send_message("❌ AI機能が無効化されています。(APIキー未設定)", ephemeral=True)
            return

        await interaction.response.defer() # 生成に時間がかかるため待機状態にする

        try:
            # タスク管理とは関係ない、純粋なチャットとして応答
            # system_instructionでキャラ付けも可能
            response = model.generate_content(prompt)
            text = response.text

            # Embedで見やすく返す
            embed = discord.Embed(title="🤖 AI Answer", description=text[:4000], color=discord.Color.green())
            embed.set_footer(text=f"Q: {prompt}")
            
            await interaction.followup.send(embed=embed)

        except Exception as e:
            await interaction.followup.send(f"❌ エラーが発生しました: {e}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(AIChatCog(bot))