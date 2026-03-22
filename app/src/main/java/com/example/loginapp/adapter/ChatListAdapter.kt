package com.example.loginapp.adapter

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.example.loginapp.R
import com.example.loginapp.model.Chat
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class ChatListAdapter(
    private val onChatClick: (Chat) -> Unit
) : ListAdapter<Chat, RecyclerView.ViewHolder>(ChatDiffCallback()) {

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_chat_list, parent, false)
        return ChatViewHolder(view)
    }

    override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
        (holder as ChatViewHolder).bind(getItem(position))
    }

    inner class ChatViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val ivProfile: ImageView = itemView.findViewById(R.id.ivProfile)
        private val tvName: TextView = itemView.findViewById(R.id.tvName)
        private val tvLastMessage: TextView = itemView.findViewById(R.id.tvLastMessage)
        private val tvTime: TextView = itemView.findViewById(R.id.tvTime)
        private val tvUnread: TextView = itemView.findViewById(R.id.tvUnreadCount)

        fun bind(chat: Chat) {
            tvName.text = chat.userName
            tvLastMessage.text = chat.lastMessage
            tvTime.text = formatTime(chat.lastMessageTime)

            if (chat.unreadCount > 0) {
                tvUnread.visibility = View.VISIBLE
                tvUnread.text = if (chat.unreadCount > 99) "99+" else chat.unreadCount.toString()
            } else {
                tvUnread.visibility = View.GONE
            }

            // Primeira letra como imagem de perfil
            ivProfile.setImageResource(R.drawable.ic_profile_placeholder)
            ivProfile.setBackgroundResource(R.drawable.bg_circle)

            itemView.setOnClickListener { onChatClick(chat) }
        }

        private fun formatTime(timestamp: Long): String {
            val now = System.currentTimeMillis()
            val diff = now - timestamp
            val oneDay = 24 * 60 * 60 * 1000

            return when {
                diff < oneDay -> {
                    SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date(timestamp))
                }
                diff < 7 * oneDay -> {
                    SimpleDateFormat("EEE", Locale.getDefault()).format(Date(timestamp))
                }
                else -> {
                    SimpleDateFormat("dd/MM/yy", Locale.getDefault()).format(Date(timestamp))
                }
            }
        }
    }

    class ChatDiffCallback : DiffUtil.ItemCallback<Chat>() {
        override fun areItemsTheSame(oldItem: Chat, newItem: Chat): Boolean {
            return oldItem.userId == newItem.userId
        }

        override fun areContentsTheSame(oldItem: Chat, newItem: Chat): Boolean {
            return oldItem == newItem
        }
    }
}
