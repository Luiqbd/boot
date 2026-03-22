package com.example.loginapp

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.Menu
import android.view.MenuItem
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import com.example.loginapp.adapter.ChatListAdapter
import com.example.loginapp.databinding.ActivityChatsBinding
import com.example.loginapp.model.Chat
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken

class ChatsActivity : AppCompatActivity() {

    private lateinit var binding: ActivityChatsBinding
    private lateinit var chatAdapter: ChatListAdapter
    private val sharedPrefs by lazy { getSharedPreferences("LoginApp", Context.MODE_PRIVATE) }

    companion object {
        private const val KEY_CHATS = "chats"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityChatsBinding.inflate(layoutInflater)
        setContentView(binding.root)

        setupToolbar()
        setupRecyclerView()
        loadChats()
    }

    private fun setupToolbar() {
        setSupportActionBar(binding.toolbar)
        supportActionBar?.title = "Conversas"
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
    }

    private fun setupRecyclerView() {
        chatAdapter = ChatListAdapter { chat ->
            openChat(chat)
        }

        binding.rvChats.apply {
            layoutManager = LinearLayoutManager(this@ChatsActivity)
            adapter = chatAdapter
        }
    }

    private fun loadChats() {
        val currentUser = sharedPrefs.getString("logged_user", "") ?: ""
        
        // Simular conversas (em um app real, viria do servidor/banco)
        val chats = getSampleChats(currentUser)
        
        chatAdapter.submitList(chats)
    }

    private fun getSampleChats(currentUser: String): List<Chat> {
        val prefs = getSharedPreferences("users_$currentUser", Context.MODE_PRIVATE)
        val chatsJson = prefs.getString("chats", null)

        return if (chatsJson != null) {
            val type = object : TypeToken<List<Chat>>() {}.type
            Gson().fromJson(chatsJson, type)
        } else {
            // Criar conversas de exemplo
            val sampleChats = listOf(
                Chat(
                    userId = "user1",
                    userName = "Maria Silva",
                    lastMessage = "Olá! Tudo bem?",
                    lastMessageTime = System.currentTimeMillis() - 300000,
                    unreadCount = 2
                ),
                Chat(
                    userId = "user2", 
                    userName = "João Santos",
                    lastMessage = "Vamos marcar aquele café?",
                    lastMessageTime = System.currentTimeMillis() - 3600000,
                    unreadCount = 0
                ),
                Chat(
                    userId = "user3",
                    userName = "Ana Paula",
                    lastMessage = "Enviado uma foto",
                    lastMessageTime = System.currentTimeMillis() - 86400000,
                    unreadCount = 0
                ),
                Chat(
                    userId = "user4",
                    userName = "Pedro Costa",
                    lastMessage = "Obrigado! 👍",
                    lastMessageTime = System.currentTimeMillis() - 172800000,
                    unreadCount = 0
                )
            )
            // Salvar para futuras referências
            prefs.edit().putString("chats", Gson().toJson(sampleChats)).apply()
            sampleChats
        }
    }

    private fun openChat(chat: Chat) {
        val intent = Intent(this, ChatActivity::class.java).apply {
            putExtra("chat_id", chat.userId)
            putExtra("chat_name", chat.userName)
        }
        startActivity(intent)
    }

    override fun onCreateOptionsMenu(menu: Menu?): Boolean {
        menuInflater.inflate(R.menu.menu_chats, menu)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        return when (item.itemId) {
            R.id.action_new_chat -> {
                Toast.makeText(this, "Nova conversa", Toast.LENGTH_SHORT).show()
                true
            }
            android.R.id.home -> {
                finish()
                true
            }
            else -> super.onOptionsItemSelected(item)
        }
    }
}
