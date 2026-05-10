module.exports=(req,res,next)=>{
  if(req.session&&req.session.admin)return next();
  res.status(401).json({message:'未登录'});
};
